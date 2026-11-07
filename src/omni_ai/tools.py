"""ToolRunner — クライアント側ツールループ（仕様書 v3.1 §8.4 / 実装設計書 §5.10）。"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, Literal

import anyio
from pydantic import BaseModel, ValidationError

from .capabilities import ChatCapability, SandboxCapability, SearchCapability
from .exceptions import ToolLoopLimitError, UnsupportedFeatureError
from .types import (
    BuiltinTool,
    ChatMessage,
    ChatResponse,
    OmniTool,
    ResponseMeta,
    ToolCall,
    ToolDefinition,
    ToolSpec,
    omni_tool,
)

__all__ = ["ToolRunResult", "ToolRunner"]

logger = logging.getLogger("omni_ai")


class ToolRunResult(BaseModel):
    response: ChatResponse  # 最後の応答
    messages: list[ChatMessage]  # ループ中に追加されたものを含む全履歴
    turns: int
    metas: list[ResponseMeta]
    pending_tool_calls: list[ToolCall] = []  # handler を持たないツールの呼び出し（呼び出し側で実行する）


def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


class ToolRunner:
    def __init__(
        self,
        chat: ChatCapability,
        *,
        search: SearchCapability | None = None,
        sandbox: SandboxCapability | None = None,
        max_turns: int = 8,
        parallel_tool_calls: bool = True,
        on_tool_error: Literal["return_to_model", "raise"] = "return_to_model",
    ) -> None:
        self.chat = chat
        self.search = search
        self.sandbox = sandbox
        self.max_turns = max_turns
        self.parallel_tool_calls = parallel_tool_calls
        self.on_tool_error = on_tool_error

    # ------------------------------------------------------------------ BuiltinTool の代行

    def _emulate(self, builtin: BuiltinTool, *, use_async: bool) -> OmniTool:
        if builtin is BuiltinTool.WEB_SEARCH:
            search = self.search
            if search is None:
                raise UnsupportedFeatureError(
                    "WEB_SEARCH needs server-side support or a SearchCapability for emulation",
                    provider=self.chat.provider_name,
                )

            def fmt(res: Any) -> str:
                return (
                    "\n\n".join(f"[{r.title}]({r.url})\n{r.content}" for r in res.results) or "(no results)"
                )

            if use_async:

                async def web_search(query: str) -> str:
                    """Search the web for up-to-date information.

                    Args:
                        query: search query
                    """
                    return fmt(await search.asearch(query, max_results=5))

                return omni_tool(web_search)

            def web_search_sync(query: str) -> str:
                """Search the web for up-to-date information.

                Args:
                    query: search query
                """
                return fmt(search.search(query, max_results=5))

            return omni_tool(name="web_search")(web_search_sync)

        sandbox = self.sandbox
        if sandbox is None:
            raise UnsupportedFeatureError(
                "CODE_EXECUTION needs server-side support or a SandboxCapability for emulation",
                provider=self.chat.provider_name,
            )

        def fmt_exec(r: Any) -> str:
            return _to_text({"stdout": r.stdout, "stderr": r.stderr, "exit_code": r.exit_code})

        if use_async:

            async def execute_python(code: str) -> str:
                """Execute Python code in an isolated sandbox and return stdout/stderr.

                Args:
                    code: Python source code
                """
                return fmt_exec(await sandbox.aexecute_python(code))

            return omni_tool(execute_python)

        def execute_python_sync(code: str) -> str:
            """Execute Python code in an isolated sandbox and return stdout/stderr.

            Args:
                code: Python source code
            """
            return fmt_exec(sandbox.execute_python(code))

        return omni_tool(name="execute_python")(execute_python_sync)

    def _resolve(
        self, tools: Sequence[ToolSpec], *, use_async: bool
    ) -> tuple[list[ToolSpec], dict[str, OmniTool], set[str]]:
        native = self.chat.supported_builtins
        api_tools: list[ToolSpec] = []
        handlers: dict[str, OmniTool] = {}
        external: set[str] = set()  # handler を持たない（呼び出し側で実行する）ツール
        for t in tools:
            if isinstance(t, BuiltinTool):
                if t in native:
                    api_tools.append(t)
                else:
                    emulated = self._emulate(t, use_async=use_async)
                    api_tools.append(emulated)
                    handlers[emulated.name] = emulated
            elif isinstance(t, OmniTool):
                api_tools.append(t)
                handlers[t.name] = t
            elif isinstance(t, ToolDefinition):
                api_tools.append(t)
                external.add(t.name)
        return api_tools, handlers, external

    # ------------------------------------------------------------------ 1 件の実行

    def _error_result(self, call: ToolCall, exc: BaseException) -> str:
        if self.on_tool_error == "raise":
            raise exc
        kind = "invalid arguments" if isinstance(exc, ValidationError) else type(exc).__name__
        logger.info("tool %s failed: %s", call.name, exc)
        return _to_text({"error": f"{kind}: {exc}"})

    def _run_one(self, call: ToolCall, handlers: dict[str, OmniTool]) -> str:
        tool = handlers.get(call.name)
        if tool is None:
            return _to_text({"error": f"unknown tool {call.name!r}"})
        try:
            if tool.is_async:
                return _to_text(anyio.run(tool.ainvoke, call.arguments))
            return _to_text(tool.invoke(call.arguments))
        except Exception as exc:
            return self._error_result(call, exc)

    async def _arun_one(self, call: ToolCall, handlers: dict[str, OmniTool]) -> str:
        tool = handlers.get(call.name)
        if tool is None:
            return _to_text({"error": f"unknown tool {call.name!r}"})
        try:
            return _to_text(await tool.ainvoke(call.arguments))
        except Exception as exc:
            return self._error_result(call, exc)

    # ------------------------------------------------------------------ ループ

    @staticmethod
    def _external_calls(res: ChatResponse, external: set[str]) -> list[ToolCall]:
        return [c for c in res.tool_calls if c.name in external]

    def run(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], **opts: Any) -> ToolRunResult:
        api_tools, handlers, external = self._resolve(tools, use_async=False)
        history = list(messages)
        metas: list[ResponseMeta] = []
        res: ChatResponse | None = None
        for turn in range(1, self.max_turns + 1):
            res = self.chat.generate(history, tools=api_tools, **opts)
            metas.append(res.meta)
            history.append(res.to_message())
            if not res.tool_calls:
                return ToolRunResult(response=res, messages=history, turns=turn, metas=metas)
            if pending := self._external_calls(res, external):
                return ToolRunResult(
                    response=res, messages=history, turns=turn, metas=metas, pending_tool_calls=pending
                )
            for call in res.tool_calls:
                history.append(
                    ChatMessage.tool(self._run_one(call, handlers), tool_call_id=call.id, name=call.name)
                )
        assert res is not None
        partial = ToolRunResult(response=res, messages=history, turns=self.max_turns, metas=metas)
        raise ToolLoopLimitError(
            f"tool loop exceeded max_turns={self.max_turns}",
            provider=self.chat.provider_name,
            partial_result=partial,
        )

    async def arun(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], **opts: Any
    ) -> ToolRunResult:
        api_tools, handlers, external = self._resolve(tools, use_async=True)
        history = list(messages)
        metas: list[ResponseMeta] = []
        res: ChatResponse | None = None
        for turn in range(1, self.max_turns + 1):
            res = await self.chat.agenerate(history, tools=api_tools, **opts)
            metas.append(res.meta)
            history.append(res.to_message())
            if not res.tool_calls:
                return ToolRunResult(response=res, messages=history, turns=turn, metas=metas)
            if pending := self._external_calls(res, external):
                return ToolRunResult(
                    response=res, messages=history, turns=turn, metas=metas, pending_tool_calls=pending
                )
            results: list[str] = [""] * len(res.tool_calls)
            if self.parallel_tool_calls and len(res.tool_calls) > 1:

                async def worker(i: int, call: ToolCall, out: list[str] = results) -> None:
                    out[i] = await self._arun_one(call, handlers)

                async with anyio.create_task_group() as tg:
                    for i, call in enumerate(res.tool_calls):
                        tg.start_soon(worker, i, call)
            else:
                for i, call in enumerate(res.tool_calls):
                    results[i] = await self._arun_one(call, handlers)
            for call, out in zip(res.tool_calls, results, strict=True):
                history.append(ChatMessage.tool(out, tool_call_id=call.id, name=call.name))
        assert res is not None
        partial = ToolRunResult(response=res, messages=history, turns=self.max_turns, metas=metas)
        raise ToolLoopLimitError(
            f"tool loop exceeded max_turns={self.max_turns}",
            provider=self.chat.provider_name,
            partial_result=partial,
        )
