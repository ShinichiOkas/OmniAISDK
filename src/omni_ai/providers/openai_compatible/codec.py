"""共通型 ⇔ OpenAI 互換 /chat/completions の JSON（I/O を持たない）。実装設計書 §25。

xAI・Agnes・OpenRouter・手元の OpenAI 互換サーバー（llama.cpp など）が同じ形を話す。
プロバイダーごとの差（構造化出力の方式など）は引数で受け取る。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from ..._http import SSEDecoder
from ..._media import to_base64
from ...exceptions import (
    AuthenticationError,
    ContextLengthExceededError,
    InvalidRequestError,
    NotFoundError,
    OmniError,
    PermissionDeniedError,
    ProviderOverloadedError,
    QuotaExceededError,
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

__all__ = [
    "DEFAULT_INLINE_LIMIT",
    "OpenAICompatibleStreamState",
    "ResponseFormatMode",
    "build_chat_body",
    "map_error",
    "parse_chat_response",
]

DEFAULT_INLINE_LIMIT = 20 * 1024 * 1024

# 構造化出力の送り方。json_schema: スキーマを渡す / json_object: JSON であることだけを指定し、
# スキーマは指示文で伝える / prompt: response_format を送らず指示文だけ（非対応のサーバー向け）
ResponseFormatMode = Literal["json_schema", "json_object", "prompt"]

# 素通しする生成パラメータ（provider_options の自分の名前空間から）
_PASSTHROUGH = (
    "top_p",
    "seed",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "reasoning_effort",
    "parallel_tool_calls",
    "user",
)


# ---------------------------------------------------------------------------- 要求


def _media_to_wire(part: MediaPart, inline_limit: int, provider: str) -> dict[str, Any]:
    if part.type != "image":
        raise UnsupportedFeatureError(
            f"{provider} /chat/completions accepts images only (got {part.type})", provider=provider
        )
    if isinstance(part.source, FileRef):
        raise UnsupportedFeatureError(
            f"{provider} does not accept FileRef; pass bytes, Path or a URL", provider=provider
        )
    if isinstance(part.source, UrlRef):
        url = part.source.url
    else:
        url = f"data:{part.mime_type};base64,{to_base64(part.source, limit=inline_limit, provider=provider)}"
    return {"type": "image_url", "image_url": {"url": url}}


def _content_to_wire(msg: ChatMessage, inline_limit: int, provider: str) -> str | list[dict[str, Any]]:
    if isinstance(msg.content, str):
        return msg.content
    if all(isinstance(p, TextPart) for p in msg.content):
        return "\n".join(p.text for p in msg.content if isinstance(p, TextPart))
    parts: list[dict[str, Any]] = []
    for p in msg.content:
        if isinstance(p, TextPart):
            parts.append({"type": "text", "text": p.text})
        else:
            parts.append(_media_to_wire(p, inline_limit, provider))
    return parts


def _message_to_wire(msg: ChatMessage, inline_limit: int, provider: str) -> dict[str, Any]:
    if msg.role is Role.TOOL:
        out: dict[str, Any] = {"role": "tool", "content": msg.text(), "tool_call_id": msg.tool_call_id or ""}
        if msg.name:
            out["name"] = msg.name
        return out
    if msg.role is Role.ASSISTANT:
        text = msg.text()
        out = {"role": "assistant", "content": text or None}
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in msg.tool_calls
            ]
        elif out["content"] is None:
            out["content"] = ""
        return out
    if msg.role is Role.SYSTEM:
        return {"role": "system", "content": msg.text()}
    return {"role": "user", "content": _content_to_wire(msg, inline_limit, provider)}


def _schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "Respond with a single JSON object only (no prose, no code fence) "
        "that conforms to this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
    )


def build_chat_body(
    req: ChatRequest,
    *,
    stream: bool,
    provider: str,
    response_format: ResponseFormatMode = "json_schema",
    stream_usage: bool = True,
) -> dict[str, Any]:
    opts = req.options
    inline_limit = int(opts.get("inline_limit", DEFAULT_INLINE_LIMIT))
    messages = [_message_to_wire(m, inline_limit, provider) for m in req.messages]
    body: dict[str, Any] = {"model": req.model, "messages": messages}
    if stream:
        body["stream"] = True
        if opts.get("stream_usage", stream_usage):
            body["stream_options"] = {"include_usage": True}
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.max_tokens is not None:
        body["max_tokens"] = req.max_tokens
    if req.functions:
        body["tools"] = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters_schema},
            }
            for t in req.functions
        ]
        if req.tool_choice in ("auto", "none", "required"):
            body["tool_choice"] = req.tool_choice
        else:
            body["tool_choice"] = {"type": "function", "function": {"name": req.tool_choice}}
    if req.response_schema is not None:
        schema = normalize_json_schema(req.response_schema.model_json_schema())
        mode = opts.get("response_format", response_format)
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": req.response_schema.__name__, "schema": schema, "strict": False},
            }
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif mode != "prompt":
            raise InvalidRequestError(f"unknown response_format mode {mode!r}", provider=provider)
        # どの方式でもスキーマを指示文で添える。response_format を黙って捨てるサーバーがある
        # （実測 2026-11-08: Ollama の /v1 は json_schema を無視して地の文を返した）
        messages.insert(0, {"role": "system", "content": _schema_instruction(schema)})
    for key in _PASSTHROUGH:
        if key in opts:
            body[key] = opts[key]
    extra = opts.get("extra_body")
    if isinstance(extra, dict):
        body.update(extra)
    return body


# ---------------------------------------------------------------------------- 応答


def map_finish(reason: str | None, has_tool_calls: bool) -> FinishReason:
    if reason in ("tool_calls", "function_call"):
        return "tool_calls"
    if reason == "stop" or reason is None:
        return "tool_calls" if has_tool_calls else "stop"
    if reason == "length":
        return "length"
    if reason == "content_filter":
        return "content_filter"
    if reason == "error":
        return "error"
    return "unknown"


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def _tool_call(raw: Mapping[str, Any]) -> ToolCall:
    fn = raw.get("function") or {}
    return ToolCall(
        id=raw.get("id") or new_tool_call_id(),
        name=fn.get("name") or "",
        arguments=_arguments(fn.get("arguments")),
    )


def _usage(raw: Mapping[str, Any] | None) -> Usage:
    if not raw:
        return Usage()
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    p_details = raw.get("prompt_tokens_details") or {}
    c_details = raw.get("completion_tokens_details") or {}
    reasoning = int(c_details.get("reasoning_tokens") or 0)
    return Usage(
        prompt_tokens=prompt,
        # 推論トークンは completion_tokens に含まれる（OpenAI の定義）→ 二重に数えないよう分ける
        completion_tokens=max(completion - reasoning, 0),
        cached_tokens=int(p_details.get("cached_tokens") or 0),
        reasoning_tokens=reasoning,
        total_tokens=int(raw.get("total_tokens") or prompt + completion),
    )


def _reasoning(msg: Mapping[str, Any]) -> str | None:
    # xAI / llama.cpp は reasoning_content、OpenRouter は reasoning
    value = msg.get("reasoning_content") or msg.get("reasoning")
    return value if isinstance(value, str) and value else None


def _in_band_error(data: Mapping[str, Any], provider: str, model: str | None) -> OmniError | None:
    err = data.get("error")
    if not err:
        return None
    status = 500
    if isinstance(err, dict) and isinstance(err.get("code"), int):
        status = err["code"]
    return map_error(status, json.dumps(data).encode(), {}, model, provider=provider)


def parse_chat_response(data: Mapping[str, Any], req: ChatRequest, *, provider: str) -> ChatResponse:
    if (err := _in_band_error(data, provider, req.model)) is not None:
        raise err
    choices = data.get("choices") or []
    if not choices:
        raise ProviderOverloadedError(
            f"{provider} returned no choices", provider=provider, model=req.model, retryable=False
        )
    choice = choices[0]
    msg = choice.get("message") or {}
    calls = [_tool_call(tc) for tc in msg.get("tool_calls") or []]
    content = msg.get("content") or None
    # JSON モード（``response_schema`` あり、または ``provider_options['response_format']='json_object'/'json'``）
    # では ``parsed`` を立てる。寛容な取り出し（フェンス剥がし・末尾カンマ修復）を使う。
    parsed: Any = None
    opts = req.options
    rf_mode = opts.get("response_format")
    if content and (req.response_schema is not None or rf_mode in ("json_object", "json", "json_schema")):
        from ..._json_extract import parse_json_response

        try:
            parsed = parse_json_response(content)
        except Exception:
            parsed = None  # 本文の text はそのまま残す
    return ChatResponse(
        content=content,
        parsed=parsed,
        reasoning=_reasoning(msg),
        tool_calls=calls,
        finish_reason=map_finish(choice.get("finish_reason"), bool(calls)),
        # 応答の model はルーティング先の名前になることがある（OpenRouter など）→ 指定した名前を記録する
        meta=ResponseMeta(
            provider=provider, model=req.model, usage=_usage(data.get("usage")), request_id=data.get("id")
        ),
    )


class OpenAICompatibleStreamState:
    """SSE（data: {...} / data: [DONE]）を共通の StreamEvent に変換する。

    ツール呼び出しは index ごとに断片（arguments の一部）が届くので、終わりで組み立ててから出す。
    """

    def __init__(self, req: ChatRequest, *, provider: str) -> None:
        self.req = req
        self.provider = provider
        self.decoder = SSEDecoder()
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: Usage | None = None
        self.request_id: str | None = None
        self.completed = False
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
            if self.finish_reason is None:
                raise ProviderOverloadedError(
                    f"{self.provider} stream ended without finish_reason",
                    provider=self.provider,
                    model=self.req.model,
                    retryable=False,
                )
            events.extend(self._complete())  # [DONE] を送らないサーバーもある
        return events

    def _handle(self, sse_events: list[Any]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        for sse in sse_events:
            if self.completed:
                break
            if sse.data.strip() == "[DONE]":
                events.extend(self._complete())
                break
            try:
                obj = sse.json()
            except json.JSONDecodeError:
                self._pending_error = ProviderOverloadedError(
                    f"malformed event in {self.provider} stream: {sse.data[:200]!r}",
                    provider=self.provider,
                    model=self.req.model,
                    retryable=False,
                )
                break
            if not isinstance(obj, dict):
                continue
            if (err := _in_band_error(obj, self.provider, self.req.model)) is not None:
                # HTTP 200 のまま、ストリームの途中でエラーが届く（OpenRouter など）
                self._pending_error = err
                break
            self.request_id = self.request_id or obj.get("id")
            if obj.get("usage"):
                self.usage = _usage(obj["usage"])
            for choice in obj.get("choices") or []:
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta") or {}
                if thinking := _reasoning(delta):
                    self.reasoning.append(thinking)
                    events.append(StreamEvent(type="reasoning_delta", delta_text=thinking))
                if text := delta.get("content"):
                    self.content.append(text)
                    events.append(StreamEvent(type="text_delta", delta_text=text))
                for raw in delta.get("tool_calls") or []:
                    slot = self.calls.setdefault(int(raw.get("index", len(self.calls))), {"args": []})
                    if raw.get("id"):
                        slot["id"] = raw["id"]
                    fn = raw.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        args = fn["arguments"]
                        slot["args"].append(args if isinstance(args, str) else json.dumps(args))
                if choice.get("finish_reason"):
                    self.finish_reason = choice["finish_reason"]
        return events

    def _complete(self) -> list[StreamEvent]:
        self.completed = True
        events: list[StreamEvent] = []
        tool_calls: list[ToolCall] = []
        for _, slot in sorted(self.calls.items()):
            tc = ToolCall(
                id=slot.get("id") or new_tool_call_id(),
                name=slot.get("name", ""),
                arguments=_arguments("".join(slot["args"])),
            )
            tool_calls.append(tc)
            events.append(StreamEvent(type="tool_call", tool_call=tc))
        finish = map_finish(self.finish_reason, bool(tool_calls))
        response = ChatResponse(
            content="".join(self.content) or None,
            reasoning="".join(self.reasoning) or None,
            tool_calls=tool_calls,
            finish_reason=finish,
            meta=ResponseMeta(
                provider=self.provider,
                model=self.req.model,
                usage=self.usage or Usage(),
                request_id=self.request_id,
            ),
        )
        events.append(
            StreamEvent(type="message_complete", finish_reason=finish, response=response, meta=response.meta)
        )
        return events


# ---------------------------------------------------------------------------- エラー

_CONTEXT_HINTS = ("context_length_exceeded", "maximum context length", "context length", "too many tokens")


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def map_error(
    status: int, body: bytes, headers: Mapping[str, str], model: str | None, *, provider: str
) -> OmniError:
    data = parse_error_json(body)
    err = data.get("error") if isinstance(data, dict) else None
    code = ""
    if isinstance(err, dict):
        message = err.get("message")
        code = str(err.get("code") or err.get("type") or "")
    else:
        message = err  # xAI などは {"error": "..."} の形もある
    message = str(message or body[:500].decode("utf-8", errors="replace") or f"HTTP {status}")
    kw: dict[str, Any] = {"provider": provider, "model": model, "status_code": status}
    lowered = f"{message} {code}".lower()
    if status in (400, 413, 422):
        if any(h in lowered for h in _CONTEXT_HINTS):
            return ContextLengthExceededError(message, **kw)
        if "not support" in lowered:
            return UnsupportedFeatureError(message, **kw)
        return InvalidRequestError(message, **kw)
    if status == 401:
        return AuthenticationError(message, **kw)
    if status == 402:  # OpenRouter: クレジット不足
        return QuotaExceededError(message, **kw)
    if status == 403:
        return PermissionDeniedError(message, **kw)
    if status == 404:
        return NotFoundError(message, **kw)
    if status == 429:
        if "quota" in lowered or "insufficient" in lowered:
            return QuotaExceededError(message, **kw)
        return RateLimitError(message, retry_after_seconds=_retry_after(headers), **kw)
    if status == 408 or status >= 500:
        return ProviderOverloadedError(message, **kw)
    return OmniError(message, **kw)
