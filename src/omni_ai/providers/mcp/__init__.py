"""MCPClientAdapter — mcp SDK 2.x による MCP サーバーへの接続（extras ``[mcp]``）。

- トランスポート: stdio（``command=[...]``）／ streamable-http（``url=...``）
- ``async with MCPClientAdapter(...) as mcp:`` で開閉する。anyio のキャンセルスコープの都合で、
  開いたタスクと同じタスクの中で使い、閉じること
- ツールのエラー（未知のツール・引数違反・ツール内の例外）は、例外ではなく
  ``MCPToolResult.is_error`` で返る（実測）
- サーバーの標準エラー出力は、既定でログファイル（OS 標準のキャッシュ配下 ``mcp/<name>.stderr.log``）に残す
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, ClassVar, TextIO

import anyio

from ...capabilities import MCPCapability
from ...compat import PlatformCompat
from ...exceptions import (
    InvalidRequestError,
    OmniError,
    ProviderConnectionError,
    ProviderTimeoutError,
    UnsupportedFeatureError,
)
from ...types import MCPAttachment, MCPToolResult, OmniTool, ToolDefinition, normalize_json_schema

__all__ = ["MCPClientAdapter", "describe_exc"]

logger = logging.getLogger("omni_ai")

DEFAULT_CALL_TIMEOUT = 120.0  # 無応答サーバーで永久に待たない
DEFAULT_CONNECT_TIMEOUT = 30.0


def describe_exc(exc: BaseException) -> str:
    """例外を 1 行に。anyio の TaskGroup が包む ExceptionGroup は展開する（素の str では原因が見えない）。"""
    subs = getattr(exc, "exceptions", None)
    if subs:
        return "; ".join(describe_exc(e) for e in subs)
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused — the server is not running"
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _mime(item: Any, default: str | None = None) -> str | None:
    # mcp 2.x は snake_case、1.x は camelCase
    return getattr(item, "mime_type", None) or getattr(item, "mimeType", None) or default


def _convert_result(result: Any) -> MCPToolResult:
    texts: list[str] = []
    attachments: list[MCPAttachment] = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", None)
        if kind == "text":
            texts.append(getattr(item, "text", "") or "")
        elif kind in ("image", "audio"):
            attachments.append(
                MCPAttachment(kind=kind, mime_type=_mime(item), data_base64=getattr(item, "data", None))
            )
        elif kind == "resource":
            res = getattr(item, "resource", None)
            res_text = getattr(res, "text", None)
            if res_text is not None:
                texts.append(str(res_text))
            else:
                attachments.append(
                    MCPAttachment(
                        kind="blob",
                        mime_type=_mime(res),
                        data_base64=getattr(res, "blob", None),
                        uri=str(getattr(res, "uri", "") or "") or None,
                    )
                )
        elif kind == "resource_link":
            attachments.append(
                MCPAttachment(kind="link", mime_type=_mime(item), uri=str(getattr(item, "uri", "")))
            )
        else:
            logger.debug("mcp: unknown content type %r ignored", kind)
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    is_error = bool(getattr(result, "is_error", None) or getattr(result, "isError", None))
    return MCPToolResult(
        text="\n".join(texts),
        is_error=is_error,
        structured=structured if isinstance(structured, dict) else None,
        attachments=attachments,
    )


class MCPClientAdapter(MCPCapability):
    provider_name: ClassVar[str] = "mcp"

    def __init__(
        self,
        name: str,
        *,
        command: Sequence[str] | None = None,
        url: str | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        headers: Mapping[str, str] | None = None,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        errlog: TextIO | Path | None = None,
        tool_prefix: str = "",
    ) -> None:
        if bool(command) == bool(url):
            raise ValueError("specify exactly one of command= (stdio) or url= (streamable-http)")
        self.name = name
        self.command = list(command or [])
        self.url = url
        self.env = dict(env) if env else None
        self.cwd = str(cwd) if cwd else None
        self.headers = dict(headers or {})
        self.call_timeout = call_timeout
        self.connect_timeout = connect_timeout
        self.errlog = errlog
        self.tool_prefix = tool_prefix
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self._tools: list[ToolDefinition] | None = None

    # ------------------------------------------------------------------ 接続

    def _errlog_stream(self, stack: AsyncExitStack) -> TextIO:
        if self.errlog is not None and not isinstance(self.errlog, Path):
            return self.errlog
        path = self.errlog or PlatformCompat.get_cache_dir() / "mcp" / f"{self.name}.stderr.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        f = path.open("a", encoding="utf-8", buffering=1)
        stack.callback(f.close)
        return f

    def _resolve_command(self) -> list[str]:
        exe = self.command[0]
        if os.path.isabs(exe) or exe == sys.executable:  # noqa: PTH117
            return list(self.command)
        # Windows の .cmd / .exe と macOS の GUI 起動時の PATH を吸収する（ADR-01, 仕様書 §9.5）
        return [PlatformCompat.resolve_executable(exe), *self.command[1:]]

    async def connect(self) -> None:
        if self._session is not None:
            return
        try:
            from mcp import ClientSession
        except ImportError as exc:  # pragma: no cover - extras 未導入
            raise ImportError('MCPClientAdapter requires mcp: pip install "omni-ai-sdk[mcp]"') from exc
        stack = AsyncExitStack()
        try:
            if self.command:
                from mcp.client.stdio import StdioServerParameters, stdio_client

                cmd = self._resolve_command()
                params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=self.env, cwd=self.cwd)
                streams = await stack.enter_async_context(
                    stdio_client(params, errlog=self._errlog_stream(stack))
                )
            else:
                from mcp.client.streamable_http import (  # type: ignore[attr-defined]  # 再公開のみ
                    create_mcp_http_client,
                    streamable_http_client,
                )

                client = create_mcp_http_client(headers=self.headers or None)
                streams = await stack.enter_async_context(
                    streamable_http_client(str(self.url), http_client=client)
                )
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            with anyio.fail_after(self.connect_timeout):
                await session.initialize()
        except FileNotFoundError as exc:
            await stack.aclose()
            raise ProviderConnectionError(
                f"MCP server {self.name!r}: {exc}", provider=self.provider_name
            ) from exc
        except TimeoutError as exc:
            await stack.aclose()
            raise ProviderTimeoutError(
                f"MCP server {self.name!r}: initialize timed out", provider=self.provider_name
            ) from exc
        except BaseException as exc:
            await stack.aclose()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ProviderConnectionError(
                f"MCP server {self.name!r}: {describe_exc(exc)}", provider=self.provider_name, raw_error=exc
            ) from exc
        self._stack = stack
        self._session = session
        self._tools = None

    async def aclose(self) -> None:
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            await stack.aclose()

    def close(self) -> None:
        if self._session is not None:
            logger.warning(
                "MCP server %r: close() cannot release an async session; use `await aclose()`", self.name
            )

    async def __aenter__(self) -> MCPClientAdapter:
        await self.connect()
        return self

    def _require(self) -> Any:
        if self._session is None:
            raise OmniError(
                f"MCP server {self.name!r} is not connected (use `async with`)", provider=self.provider_name
            )
        return self._session

    # ------------------------------------------------------------------ ツール

    async def alist_tools(self) -> list[ToolDefinition]:
        if self._tools is not None:
            return list(self._tools)
        session = self._require()
        tools: list[ToolDefinition] = []
        cursor: str | None = None
        while True:
            if cursor is None:
                page = await session.list_tools()
            else:
                from mcp import types

                page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
            for t in page.tools:
                schema = (
                    getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {"type": "object"}
                )
                tools.append(
                    ToolDefinition(
                        name=f"{self.tool_prefix}{t.name}",
                        description=t.description or "",
                        parameters_schema=normalize_json_schema(dict(schema)),
                    )
                )
            cursor = getattr(page, "next_cursor", None) or getattr(page, "nextCursor", None)
            if not cursor:
                break
        self._tools = tools
        return list(tools)

    async def acall_tool(
        self, name: str, arguments: dict[str, Any], *, timeout: float | None = None
    ) -> MCPToolResult:
        session = self._require()
        raw_name = (
            name[len(self.tool_prefix) :] if self.tool_prefix and name.startswith(self.tool_prefix) else name
        )
        from mcp.shared.exceptions import McpError

        try:
            effective = timeout if timeout is not None else self.call_timeout
            # mcp 2.x では read_timeout_seconds は timedelta を要求する
            result = await session.call_tool(
                raw_name,
                arguments,
                read_timeout_seconds=_dt.timedelta(seconds=float(effective)),
            )
        except McpError as exc:
            # 実測: 旧版は "...timed out"、新版は "Timed out while waiting for response..."
            if "timed out" in str(exc).lower():
                raise ProviderTimeoutError(
                    f"MCP {self.name}/{raw_name}: {exc}", provider=self.provider_name
                ) from exc
            raise InvalidRequestError(
                f"MCP {self.name}/{raw_name}: {exc}", provider=self.provider_name
            ) from exc
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream) as exc:
            raise ProviderConnectionError(
                f"MCP server {self.name!r} disconnected: {describe_exc(exc)}", provider=self.provider_name
            ) from exc
        if not hasattr(result, "content"):
            raise UnsupportedFeatureError(
                f"MCP {self.name}/{raw_name} returned {type(result).__name__} "
                "(input-required flows are not supported)",
                provider=self.provider_name,
            )
        return _convert_result(result)

    async def atools(self) -> list[OmniTool]:
        """ToolRunner にそのまま渡せる OmniTool の一覧（handler は MCP の call_tool）。"""
        out: list[OmniTool] = []
        for d in await self.alist_tools():

            async def handler(_name: str = d.name, **kwargs: Any) -> str:
                return (await self.acall_tool(_name, kwargs)).for_model()

            out.append(
                OmniTool(
                    name=d.name,
                    description=d.description,
                    parameters_schema=d.parameters_schema,
                    handler=handler,
                    is_async=True,
                )
            )
        return out
