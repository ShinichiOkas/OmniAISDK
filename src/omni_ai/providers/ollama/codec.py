"""共通型 ⇔ Ollama ネイティブ /api/chat の JSON（I/O を持たない）。実装設計書 §8。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..._http import MalformedLine, NDJSONDecoder
from ..._media import to_base64
from ...exceptions import (
    AuthenticationError,
    InvalidRequestError,
    NotFoundError,
    OmniError,
    ProviderOverloadedError,
    RateLimitError,
    UnsupportedFeatureError,
)
from ...types import (
    ChatMessage,
    ChatResponse,
    FileRef,
    FinishReason,
    MediaPart,
    ResponseMeta,
    Role,
    StreamEvent,
    TextPart,
    ToolCall,
    UrlRef,
    Usage,
    normalize_json_schema,
)
from ...types.tools import new_tool_call_id
from .._base import ChatRequest, parse_error_json

PROVIDER = "ollama"
DEFAULT_INLINE_LIMIT = 20 * 1024 * 1024

__all__ = [
    "OllamaStreamState",
    "build_chat_body",
    "map_error",
    "parse_chat_response",
    "required_capabilities",
]


# ---------------------------------------------------------------------------- 要求


def _message_to_wire(msg: ChatMessage, inline_limit: int) -> dict[str, Any]:
    out: dict[str, Any] = {"role": msg.role.value}
    texts: list[str] = []
    images: list[str] = []
    if isinstance(msg.content, str):
        texts.append(msg.content)
    else:
        for part in msg.content:
            if isinstance(part, TextPart):
                texts.append(part.text)
            elif isinstance(part, MediaPart):
                if part.type != "image":
                    raise UnsupportedFeatureError(
                        f"Ollama /api/chat accepts images only (got {part.type})", provider=PROVIDER
                    )
                if isinstance(part.source, (UrlRef, FileRef)):
                    raise UnsupportedFeatureError(
                        "Ollama accepts base64 images only; pass bytes, Path or a stream", provider=PROVIDER
                    )
                images.append(to_base64(part.source, limit=inline_limit, provider=PROVIDER))
    out["content"] = "\n".join(texts)
    if images:
        out["images"] = images
    if msg.role is Role.ASSISTANT and msg.tool_calls:
        calls = []
        for i, tc in enumerate(msg.tool_calls):
            fn: dict[str, Any] = {"index": i, "name": tc.name, "arguments": tc.arguments}
            call: dict[str, Any] = {"type": "function", "function": fn}
            if (orig := tc.provider_state.get(PROVIDER, {}).get("id")) is not None:
                call["id"] = orig
            calls.append(call)
        out["tool_calls"] = calls
    if msg.role is Role.TOOL:
        if msg.name:
            out["tool_name"] = msg.name
        if msg.tool_call_id:
            out["tool_call_id"] = msg.tool_call_id
    return out


def build_chat_body(req: ChatRequest, *, stream: bool) -> dict[str, Any]:
    opts = req.options
    inline_limit = int(opts.get("inline_limit", DEFAULT_INLINE_LIMIT))
    body: dict[str, Any] = {
        "model": req.model,
        "messages": [_message_to_wire(m, inline_limit) for m in req.messages],
        "stream": stream,
    }
    if req.functions and req.tool_choice != "none":
        if req.tool_choice != "auto":
            raise UnsupportedFeatureError(
                f"Ollama /api/chat has no tool_choice (requested {req.tool_choice!r})",
                provider=PROVIDER,
                model=req.model,
            )
        body["tools"] = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters_schema},
            }
            for t in req.functions
        ]
    if req.response_schema is not None:
        body["format"] = normalize_json_schema(req.response_schema.model_json_schema())
    options: dict[str, Any] = dict(opts.get("options", {}))
    if req.temperature is not None:
        options["temperature"] = req.temperature
    if req.max_tokens is not None:
        options["num_predict"] = req.max_tokens
    if "num_ctx" in opts:
        options["num_ctx"] = opts["num_ctx"]
    if options:
        body["options"] = options
    for key in ("think", "keep_alive"):
        if key in opts:
            body[key] = opts[key]
    return body


def required_capabilities(req: ChatRequest) -> set[str]:
    """この要求に必要な /api/show の capabilities。"""
    need: set[str] = set()
    if req.functions and req.tool_choice != "none":
        need.add("tools")
    for m in req.messages:
        if not isinstance(m.content, str) and any(
            isinstance(p, MediaPart) and p.type == "image" for p in m.content
        ):
            need.add("vision")
            break
    if req.options.get("think"):
        need.add("thinking")
    return need


# ---------------------------------------------------------------------------- 応答


def _finish(done_reason: str | None, has_tool_calls: bool) -> FinishReason:
    if done_reason == "stop" or (done_reason is None and has_tool_calls):
        return "tool_calls" if has_tool_calls else "stop"
    if done_reason == "length":
        return "length"
    return "unknown"


def _tool_call(raw: Mapping[str, Any]) -> ToolCall:
    fn = raw.get("function", {})
    args = fn.get("arguments", {})
    if isinstance(args, str):  # 念のため（資料では object）
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    orig_id = raw.get("id")
    return ToolCall(
        id=orig_id or new_tool_call_id(),
        name=fn.get("name", ""),
        arguments=args if isinstance(args, dict) else {"_raw": args},
        provider_state={PROVIDER: {"id": orig_id}} if orig_id else {},
    )


def _usage(data: Mapping[str, Any]) -> Usage:
    prompt = int(data.get("prompt_eval_count") or 0)
    completion = int(data.get("eval_count") or 0)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=int(data.get("prompt_eval_cached_count") or 0),
        total_tokens=prompt + completion,
    )


def parse_chat_response(data: Mapping[str, Any], req: ChatRequest) -> ChatResponse:
    if "error" in data:
        raise map_error(200, json.dumps(data).encode(), {}, req.model)
    msg = data.get("message") or {}
    calls = [_tool_call(tc) for tc in msg.get("tool_calls") or []]
    return ChatResponse(
        content=msg.get("content") or None,
        reasoning=msg.get("thinking") or None,
        tool_calls=calls,
        finish_reason=_finish(data.get("done_reason"), bool(calls)),
        meta=ResponseMeta(provider=PROVIDER, model=data.get("model") or req.model, usage=_usage(data)),
    )


class OllamaStreamState:
    """NDJSON を共通の StreamEvent に変換する。"""

    def __init__(self, req: ChatRequest) -> None:
        self.req = req
        self.decoder = NDJSONDecoder()
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: list[ToolCall] = []
        self.completed = False
        # エラー行より前のイベントを先に返し、エラーは次の呼び出しで送出する
        # （チャンクの区切り方で挙動が変わらないように）
        self._pending_error: OmniError | None = None

    def _raise_pending(self) -> None:
        if self._pending_error is not None:
            raise self._pending_error

    def feed(self, chunk: bytes) -> list[StreamEvent]:
        self._raise_pending()
        events = self._handle(self.decoder.feed(chunk))
        if not events:
            self._raise_pending()
        return events

    def finish(self) -> list[StreamEvent]:
        self._raise_pending()
        events = self._handle(self.decoder.flush())
        self._raise_pending()
        if not self.completed:
            raise ProviderOverloadedError(
                "Ollama stream ended without a final (done=true) message",
                provider=PROVIDER,
                model=self.req.model,
                retryable=False,
            )
        return events

    def _handle(self, objs: list[Any]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        for obj in objs:
            if isinstance(obj, MalformedLine):
                self._pending_error = ProviderOverloadedError(
                    f"malformed line in Ollama stream: {obj.text!r}",
                    provider=PROVIDER,
                    model=self.req.model,
                    retryable=False,
                )
                break
            if not isinstance(obj, dict):
                continue
            if "error" in obj:
                # HTTP 200 のまま、行の中でエラーが届く
                self._pending_error = map_error(500, json.dumps(obj).encode(), {}, self.req.model)
                break
            msg = obj.get("message") or {}
            if thinking := msg.get("thinking"):
                self.reasoning.append(thinking)
                events.append(StreamEvent(type="reasoning_delta", delta_text=thinking))
            if text := msg.get("content"):
                self.content.append(text)
                events.append(StreamEvent(type="text_delta", delta_text=text))
            for raw in msg.get("tool_calls") or []:
                tc = _tool_call(raw)
                self.tool_calls.append(tc)
                events.append(StreamEvent(type="tool_call", tool_call=tc))
            if obj.get("done"):
                self.completed = True
                finish = _finish(obj.get("done_reason"), bool(self.tool_calls))
                response = ChatResponse(
                    content="".join(self.content) or None,
                    reasoning="".join(self.reasoning) or None,
                    tool_calls=list(self.tool_calls),
                    finish_reason=finish,
                    meta=ResponseMeta(
                        provider=PROVIDER, model=obj.get("model") or self.req.model, usage=_usage(obj)
                    ),
                )
                events.append(
                    StreamEvent(
                        type="message_complete", finish_reason=finish, response=response, meta=response.meta
                    )
                )
        return events


# ---------------------------------------------------------------------------- エラー

_NON_TRANSIENT_500 = ("runner failed", "unsupported architecture", "failed to load")


def map_error(status: int, body: bytes, headers: Mapping[str, str], model: str | None) -> OmniError:
    data = parse_error_json(body)
    message = data.get("error") if isinstance(data, dict) else None
    message = str(message or body[:500].decode("utf-8", errors="replace") or f"HTTP {status}")
    kw: dict[str, Any] = {"provider": PROVIDER, "model": model, "status_code": status}
    lowered = message.lower()
    if status == 400:
        if "does not support" in lowered:
            return UnsupportedFeatureError(message, **kw)
        return InvalidRequestError(message, **kw)
    if status in (401, 403):
        return AuthenticationError(message, **kw)
    if status == 404:
        return NotFoundError(message, **kw)
    if status == 429:
        retry_after = headers.get("retry-after")
        try:
            seconds = float(retry_after) if retry_after else None
        except ValueError:
            seconds = None
        return RateLimitError(message, retry_after_seconds=seconds, **kw)
    if status >= 500:
        # モデルの起動失敗は何度送っても直らない → 再試行しない（フォールバックはする）
        transient = not any(s in lowered for s in _NON_TRANSIENT_500)
        return ProviderOverloadedError(message, retryable=transient, **kw)
    return OmniError(message, **kw)
