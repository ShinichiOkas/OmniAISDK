"""ToolRunner — クライアント側ツールループ（仕様書 v3.1 §8.4 / 実装設計書 §5.10）。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import ThreadPoolExecutor
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
    StreamEvent,
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
                # 既にループが走る環境（Jupyter 等）でも動くよう、別スレッドの新しいループで実行する
                with ThreadPoolExecutor(max_workers=1) as pool:
                    return _to_text(pool.submit(anyio.run, tool.ainvoke, call.arguments).result())
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
            internal = [c for c in res.tool_calls if c.name not in external]
            for call in internal:
                history.append(
                    ChatMessage.tool(self._run_one(call, handlers), tool_call_id=call.id, name=call.name)
                )
            if pending := self._external_calls(res, external):
                # 内部ツールは実行済み。外部ツールの結果は呼び出し側が履歴に足して続ける
                return ToolRunResult(
                    response=res, messages=history, turns=turn, metas=metas, pending_tool_calls=pending
                )
        assert res is not None
        partial = ToolRunResult(response=res, messages=history, turns=self.max_turns, metas=metas)
        raise ToolLoopLimitError(
            f"tool loop exceeded max_turns={self.max_turns}",
            provider=self.chat.provider_name,
            partial_result=partial,
        )

    async def _arun_many(self, calls: list[ToolCall], handlers: dict[str, OmniTool]) -> list[str]:
        results: list[str] = [""] * len(calls)
        if not (self.parallel_tool_calls and len(calls) > 1):
            for i, call in enumerate(calls):
                results[i] = await self._arun_one(call, handlers)
            return results
        errors: list[BaseException] = []

        async def worker(i: int, call: ToolCall) -> None:
            try:
                results[i] = await self._arun_one(call, handlers)
            except Exception as exc:  # on_tool_error="raise" のとき。ExceptionGroup で包まずに返す
                errors.append(exc)

        async with anyio.create_task_group() as tg:
            for i, call in enumerate(calls):
                tg.start_soon(worker, i, call)
        if errors:
            raise errors[0]
        return results

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
            internal = [c for c in res.tool_calls if c.name not in external]
            outputs = await self._arun_many(internal, handlers)
            for call, out in zip(internal, outputs, strict=True):
                history.append(ChatMessage.tool(out, tool_call_id=call.id, name=call.name))
            if pending := self._external_calls(res, external):
                return ToolRunResult(
                    response=res, messages=history, turns=turn, metas=metas, pending_tool_calls=pending
                )
        assert res is not None
        partial = ToolRunResult(response=res, messages=history, turns=self.max_turns, metas=metas)
        raise ToolLoopLimitError(
            f"tool loop exceeded max_turns={self.max_turns}",
            provider=self.chat.provider_name,
            partial_result=partial,
        )

    # ------------------------------------------------------------------ ストリームしながらツール

    async def astream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        *,
        on_max_turns_summary: bool = False,
        **opts: Any,
    ) -> AsyncIterator[StreamEvent]:
        """ストリームしながらツールを回す。チャンク・ツール呼び出し・ツール結果を順に流す。

        流れるイベント:
          - ``text_delta`` / ``reasoning_delta``: プロバイダから届いた断片
          - ``tool_call``: モデルがツールを呼ぼうとしている
          - ``message_complete``: 1 ターンのプロバイダ応答が完結した（内部ツールの実行はこの直後）
          - ``tool_result`` / ``tool_error``: 内部ツールの実行結果（名前・id・出力つき）
          - ``turn_complete``: 1 ターンの全体が片付いた（ツール結果まで履歴に積んだ）
          - ``max_turns``: 上限に達した（この後、締めの要約を 1 回だけ流して終わる。無効なら流さない）

        - 内部ツール（``@omni_tool``）は実行する。外部ツール（``ToolDefinition``）は呼び出し側が実行する
          （この astream は内部ツールのうしろで止まらず、外部ツール呼び出しは ``tool_call`` を流して
          ターンを閉じ、呼び出し側が履歴に結果を足して再度 astream を呼ぶ想定）
        - ``on_max_turns_summary=True`` にすると、上限に達したあと 1 回だけ **ツール無しの応答** を
          ストリームで流す（上限に達した後の「ツール無しの締め」。流し終わると ``turn_complete`` で閉じる）
        """
        api_tools, handlers, external = self._resolve(tools, use_async=True)
        history = list(messages)
        metas: list[ResponseMeta] = []

        for turn in range(1, self.max_turns + 1):
            final_response: ChatResponse | None = None
            async for event in self.chat.astream(history, tools=api_tools, **opts):
                yield event
                if event.type == "message_complete" and event.response is not None:
                    final_response = event.response
            if final_response is None:
                return
            metas.append(final_response.meta)
            history.append(final_response.to_message())

            if not final_response.tool_calls:
                yield StreamEvent(type="turn_complete", response=final_response, meta=final_response.meta)
                return

            internal = [c for c in final_response.tool_calls if c.name not in external]
            if internal:
                outputs = await self._arun_many(internal, handlers)
                for call, out in zip(internal, outputs, strict=True):
                    history.append(ChatMessage.tool(out, tool_call_id=call.id, name=call.name))
                    yield StreamEvent(
                        type="tool_result",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        tool_output=out,
                    )

            if self._external_calls(final_response, external):
                # 外部ツールが残った：ターンを閉じて、呼び出し側に戻す
                yield StreamEvent(
                    type="turn_complete", response=final_response, meta=final_response.meta
                )
                return

            yield StreamEvent(type="turn_complete", response=final_response, meta=final_response.meta)

        # 上限に達した
        yield StreamEvent(type="max_turns")
        if on_max_turns_summary:
            # ツール無しで 1 回だけ応答させる（上限に達した後の締め）
            summary_opts = {k: v for k, v in opts.items() if k not in ("tools",)}
            final_response = None
            async for event in self.chat.astream(history, tools=[], **summary_opts):
                yield event
                if event.type == "message_complete" and event.response is not None:
                    final_response = event.response
            if final_response is not None:
                yield StreamEvent(
                    type="turn_complete", response=final_response, meta=final_response.meta
                )
            return
        # 要約を流さない設定なら、ToolLoopLimitError と同じ意味で終わる
        raise ToolLoopLimitError(
            f"tool loop exceeded max_turns={self.max_turns}",
            provider=self.chat.provider_name,
            partial_result=None,
        )
