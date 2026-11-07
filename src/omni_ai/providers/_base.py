"""Chat アダプターの共通骨格（実装設計書 §6）。

プロバイダ固有の処理は、I/O を持たない codec 系のフック（``_build_body`` / ``_parse_body`` /
``_new_stream_state`` / ``_map_error``）に閉じ込め、送受信・再試行・ストリーム中断の扱い・
構造化出力の検証はここで共通に行う。
"""

from __future__ import annotations

import json
import logging
import re
from abc import abstractmethod
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from .._http import AsyncTransport, RetryPolicy, SyncTransport
from ..capabilities import ChatCapability, ToolChoice
from ..exceptions import (
    InvalidRequestError,
    OmniError,
    SchemaValidationError,
    StreamInterruptedError,
    UnsupportedFeatureError,
)
from ..types import BuiltinTool, ChatMessage, ChatResponse, StreamEvent, ToolDefinition, ToolSpec

__all__ = ["BaseChatAdapter", "ChatRequest", "StreamState"]

logger = logging.getLogger("omni_ai")


@dataclass
class ChatRequest:
    """正規化した 1 回分の要求。"""

    messages: list[ChatMessage]
    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    functions: list[ToolDefinition] = field(default_factory=list)
    builtins: list[BuiltinTool] = field(default_factory=list)
    tool_choice: ToolChoice = "auto"
    response_schema: type[BaseModel] | None = None
    timeout: float | None = None
    options: dict[str, Any] = field(default_factory=dict)  # このプロバイダの名前空間の指定


class StreamState(Protocol):
    def feed(self, chunk: bytes) -> list[StreamEvent]: ...

    def finish(self) -> list[StreamEvent]: ...


class BaseChatAdapter(ChatCapability):
    """送受信と共通の後処理を受け持つ。"""

    def __init__(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        default_model: str | None = None,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.default_model = default_model
        common: dict[str, Any] = {
            "provider": self.provider_name,
            "base_url": base_url,
            "headers": headers,
            "timeout": timeout,
            "retry": retry,
            "error_mapper": self._map_error_default,
        }
        self._sync = SyncTransport(client=http_client, **common)
        self._async = AsyncTransport(client=async_http_client, **common)

    # ------------------------------------------------------------------ hooks

    @abstractmethod
    def _endpoint(self, req: ChatRequest, *, stream: bool) -> str: ...

    @abstractmethod
    def _build_body(self, req: ChatRequest, *, stream: bool) -> dict[str, Any]: ...

    @abstractmethod
    def _parse_body(self, data: Any, req: ChatRequest) -> ChatResponse: ...

    @abstractmethod
    def _new_stream_state(self, req: ChatRequest) -> StreamState: ...

    @abstractmethod
    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError: ...

    def _preflight(self, req: ChatRequest) -> None:
        """送信前の同期チェック（例: Ollama の能力確認）。"""

    async def _apreflight(self, req: ChatRequest) -> None:
        """送信前の非同期チェック。"""

    def _map_error_default(self, status: int, body: bytes, headers: Mapping[str, str]) -> OmniError:
        return self._map_error(status, body, headers, None)

    # ------------------------------------------------------------------ 要求の正規化

    def _prepare(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        tools: Sequence[ToolSpec] | None,
        tool_choice: ToolChoice,
        response_schema: type[BaseModel] | None,
        timeout: float | None,
        provider_options: dict[str, Any],
    ) -> ChatRequest:
        resolved = model or self.default_model
        if not resolved:
            raise InvalidRequestError(
                f"model is required ({self.provider_name} adapter has no default_model)",
                provider=self.provider_name,
            )
        functions: list[ToolDefinition] = []
        builtins: list[BuiltinTool] = []
        for t in tools or ():
            if isinstance(t, BuiltinTool):
                if t not in self.supported_builtins:
                    raise UnsupportedFeatureError(
                        f"{t.value} is not executed server-side by {self.provider_name}; "
                        "use ToolRunner (ai.tools.arun) to emulate it client-side",
                        provider=self.provider_name,
                        model=resolved,
                    )
                builtins.append(t)
            elif isinstance(t, ToolDefinition):
                functions.append(t)
            else:
                raise TypeError(f"unsupported tool spec: {t!r}")
        ours: dict[str, Any] = {}
        for key, value in provider_options.items():
            if key == self.provider_name and isinstance(value, dict):
                ours = dict(value)
            else:
                # 他社の名前空間・未知のキーは捨てるが、黙って捨てない（縮退経路のログ）
                logger.debug("%s adapter ignored provider option %r", self.provider_name, key)
        return ChatRequest(
            messages=list(messages),
            model=resolved,
            temperature=temperature,
            max_tokens=max_tokens,
            functions=functions,
            builtins=builtins,
            tool_choice=tool_choice,
            response_schema=response_schema,
            timeout=timeout,
            options=ours,
        )

    # ------------------------------------------------------------------ 後処理

    def _finalize(self, res: ChatResponse, req: ChatRequest, attempts: int) -> ChatResponse:
        res.meta.attempts = attempts
        if req.response_schema is not None and res.content is not None and res.finish_reason != "tool_calls":
            try:
                res.parsed = req.response_schema.model_validate_json(strip_code_fence(res.content))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise SchemaValidationError(
                    f"response did not match {req.response_schema.__name__}: {exc}",
                    provider=self.provider_name,
                    model=res.meta.model,
                    raw_error=exc,
                ) from exc
        return res

    def _error_mapper_for(self, model: str) -> Any:
        def mapper(status: int, body: bytes, headers: Mapping[str, str]) -> OmniError:
            err = self._map_error(status, body, headers, model)
            err.model = err.model or model
            return err

        return mapper

    def _complete(self, ev: StreamEvent, req: ChatRequest, attempts: int) -> StreamEvent:
        """message_complete を、feed / finish のどちらから出たかに関係なく後処理する。"""
        if ev.type == "message_complete" and ev.response is not None:
            self._finalize(ev.response, req, attempts)
            ev.meta = ev.response.meta
        return ev

    def _interrupted(self, exc: OmniError, req: ChatRequest, emitted: bool) -> OmniError:
        # 構造化出力の検証失敗は通信の失敗ではないので、そのまま返す
        if emitted and not isinstance(exc, (StreamInterruptedError, SchemaValidationError)):
            err = StreamInterruptedError(
                f"stream interrupted after partial output: {exc.message}",
                provider=self.provider_name,
                model=req.model,
                raw_error=exc,
            )
            return err
        return exc

    # ------------------------------------------------------------------ 同期

    def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> ChatResponse:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        self._preflight(req)
        result = self._sync.request(
            "POST",
            self._endpoint(req, stream=False),
            json=self._build_body(req, stream=False),
            timeout=req.timeout,
            error_mapper=self._error_mapper_for(req.model),
        )
        return self._finalize(self._parse_body(result.json(), req), req, result.attempts)

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> Iterator[StreamEvent]:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        return self._stream_sync(req)

    def _stream_sync(self, req: ChatRequest) -> Iterator[StreamEvent]:
        self._preflight(req)
        emitted = False
        with self._sync.stream(
            "POST",
            self._endpoint(req, stream=True),
            json=self._build_body(req, stream=True),
            timeout=req.timeout,
            error_mapper=self._error_mapper_for(req.model),
        ) as handle:
            state = self._new_stream_state(req)
            try:
                for chunk in self._sync.iter_bytes(handle):
                    for ev in state.feed(chunk):
                        yield self._complete(ev, req, handle.attempts)
                        emitted = True
                for ev in state.finish():
                    yield self._complete(ev, req, handle.attempts)
                    emitted = True
            except OmniError as exc:
                err = self._interrupted(exc, req, emitted)
                if err is exc:
                    raise
                raise err from exc

    # ------------------------------------------------------------------ 非同期

    async def agenerate(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> ChatResponse:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        await self._apreflight(req)
        result = await self._async.request(
            "POST",
            self._endpoint(req, stream=False),
            json=self._build_body(req, stream=False),
            timeout=req.timeout,
            error_mapper=self._error_mapper_for(req.model),
        )
        return self._finalize(self._parse_body(result.json(), req), req, result.attempts)

    def astream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> AsyncIterator[StreamEvent]:
        req = self._prepare(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice=tool_choice,
            response_schema=response_schema,
            timeout=timeout,
            provider_options=provider_options,
        )
        return self._stream_async(req)

    async def _stream_async(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        await self._apreflight(req)
        emitted = False
        async with self._async.stream(
            "POST",
            self._endpoint(req, stream=True),
            json=self._build_body(req, stream=True),
            timeout=req.timeout,
            error_mapper=self._error_mapper_for(req.model),
        ) as handle:
            state = self._new_stream_state(req)
            chunks = self._async.aiter_bytes(handle)
            try:
                async for chunk in chunks:
                    for ev in state.feed(chunk):
                        yield self._complete(ev, req, handle.attempts)
                        emitted = True
                for ev in state.finish():
                    yield self._complete(ev, req, handle.attempts)
                    emitted = True
            except OmniError as exc:
                err = self._interrupted(exc, req, emitted)
                if err is exc:
                    raise
                raise err from exc
            finally:
                # 利用者が途中で読むのをやめても、内側の非同期ジェネレータを確実に閉じる
                await chunks.aclose()

    # ------------------------------------------------------------------ 終了処理

    def close(self) -> None:
        """同期 transport を閉じ、非同期 transport も以後使えなくする。

        非同期クライアントの接続は await しないと閉じられないため、作成済みなら aclose() を案内する。
        """
        self._sync.close()
        self._async.mark_closed_sync()

    async def aclose(self) -> None:
        await self._async.aclose()
        self._sync.close()


_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """応答全体が 1 組のコードフェンスで包まれていれば外す（Ollama 実測: format 指定でも包まれうる）。"""
    m = _FENCE.match(text)
    return m.group(1) if m else text


def parse_error_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", errors="replace") or "null")
    except json.JSONDecodeError:
        return None
