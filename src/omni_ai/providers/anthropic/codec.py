"""共通型 ⇔ Anthropic Messages API (``POST /v1/messages``) の JSON（I/O を持たない）。

- 送るのは呼び出し側が指定したものだけ。``temperature`` などをモデルごとに書き換えない
  （新しいモデルは既定外のサンプリング指定や強制の ``tool_choice`` を 400 で断る。その 400 は
  :class:`UnsupportedFeatureError` に写す）。
- 応答の ``content`` ブロック列は ``provider_state`` にそのまま残し、次の要求でそのまま返す
  （thinking ブロックの署名は、変えずに返さないと 400 になる）。
- ``usage.input_tokens`` はキャッシュ分を含まない。``prompt_tokens`` は
  ``input + cache_read + cache_creation`` にそろえる（他のプロバイダーの定義と同じ）。
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from typing import Any

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
    "DEFAULT_MAX_TOKENS",
    "PROVIDER",
    "AnthropicStreamState",
    "build_body",
    "map_error",
    "parse_response",
]

PROVIDER = "anthropic"
DEFAULT_INLINE_LIMIT = 20 * 1024 * 1024
# max_tokens は API の必須項目。思考ぶんも含む上限なので、小さすぎると length で切れる
DEFAULT_MAX_TOKENS = 16_000

# 素通しする生成パラメータ（provider_options["anthropic"] から）
_PASSTHROUGH = ("top_p", "top_k", "stop_sequences", "thinking", "metadata", "service_tier", "container")

_ID_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


def safe_id(raw: str) -> str:
    """tool_use の id は ``[a-zA-Z0-9_-]+`` だけ。他社の id が来ても同じ変換で往復できるようにする。"""
    return _ID_UNSAFE.sub("_", raw) or new_tool_call_id()


# ---------------------------------------------------------------------------- 要求


def _media_block(part: MediaPart, inline_limit: int) -> dict[str, Any]:
    if part.type not in ("image", "document"):
        raise UnsupportedFeatureError(
            f"anthropic Messages accepts images and PDF documents only (got {part.type})", provider=PROVIDER
        )
    if isinstance(part.source, FileRef):
        raise UnsupportedFeatureError(
            "anthropic does not accept FileRef here; pass bytes, Path or a URL", provider=PROVIDER
        )
    if part.type == "document" and part.mime_type != "application/pdf":
        raise UnsupportedFeatureError(
            f"anthropic documents must be application/pdf (got {part.mime_type})", provider=PROVIDER
        )
    if isinstance(part.source, UrlRef):
        source: dict[str, Any] = {"type": "url", "url": part.source.url}
    else:
        source = {
            "type": "base64",
            "media_type": part.mime_type,
            "data": to_base64(part.source, limit=inline_limit, provider=PROVIDER),
        }
    return {"type": part.type, "source": source}


def _user_blocks(msg: ChatMessage, inline_limit: int) -> list[dict[str, Any]]:
    if isinstance(msg.content, str):
        return [{"type": "text", "text": msg.content}] if msg.content else []
    blocks: list[dict[str, Any]] = []
    for p in msg.content:
        if isinstance(p, TextPart):
            if p.text:
                blocks.append({"type": "text", "text": p.text})
        elif isinstance(p, MediaPart):
            blocks.append(_media_block(p, inline_limit))
    return blocks


def _assistant_blocks(msg: ChatMessage) -> list[dict[str, Any]]:
    stored = msg.provider_state.get(PROVIDER, {}).get("content")
    if isinstance(stored, list) and stored:
        # thinking の署名などを欠かさないため、モデルの応答をそのまま返す
        return copy.deepcopy(stored)
    blocks: list[dict[str, Any]] = []
    if text := msg.text():
        blocks.append({"type": "text", "text": text})
    for tc in msg.tool_calls or []:
        blocks.append({"type": "tool_use", "id": safe_id(tc.id), "name": tc.name, "input": tc.arguments})
    return blocks


def build_messages(messages: list[ChatMessage], inline_limit: int) -> tuple[str | None, list[dict[str, Any]]]:
    """(system, messages)。連続する同じ役割は 1 つにまとめる（ツール結果は 1 つの user に並べる）。"""
    system_texts: list[str] = []
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for msg in messages:
        if msg.role is Role.SYSTEM:
            if text := msg.text():
                system_texts.append(text)
        elif msg.role is Role.TOOL:
            if not msg.tool_call_id:
                raise InvalidRequestError(
                    "tool message for anthropic requires `tool_call_id`", provider=PROVIDER
                )
            push(
                "user",
                [{"type": "tool_result", "tool_use_id": safe_id(msg.tool_call_id), "content": msg.text()}],
            )
        elif msg.role is Role.ASSISTANT:
            push("assistant", _assistant_blocks(msg))
        else:
            blocks = _user_blocks(msg, inline_limit)
            # tool_result は同じ user の先頭に置く決まり
            if out and out[-1]["role"] == "user":
                results = [b for b in out[-1]["content"] if b["type"] == "tool_result"]
                if results and len(results) == len(out[-1]["content"]):
                    out[-1]["content"].extend(blocks)
                    continue
            push("user", blocks)
    return ("\n\n".join(system_texts) or None), out


def _schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "Respond with a single JSON object only (no prose, no code fence) "
        "that conforms to this JSON Schema:\n" + json.dumps(schema, ensure_ascii=False)
    )


def _strict_object_schema(node: Any) -> Any:
    """構造化出力は object ごとに ``additionalProperties: false`` を求める。"""
    if isinstance(node, list):
        return [_strict_object_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: (_strict_object_schema(v) if k != "enum" and k != "const" else v) for k, v in node.items()}
    if out.get("type") == "object" or "properties" in out:
        out.setdefault("additionalProperties", False)
    return out


def _tool_choice(choice: str) -> dict[str, Any]:
    if choice in ("auto", "none"):
        return {"type": choice}
    if choice == "required":
        return {"type": "any"}
    return {"type": "tool", "name": choice}


def build_body(
    req: ChatRequest, *, stream: bool, default_max_tokens: int = DEFAULT_MAX_TOKENS
) -> dict[str, Any]:
    opts = req.options
    inline_limit = int(opts.get("inline_limit", DEFAULT_INLINE_LIMIT))
    system, messages = build_messages(req.messages, inline_limit)
    body: dict[str, Any] = {
        "model": req.model,
        "max_tokens": req.max_tokens if req.max_tokens is not None else default_max_tokens,
        "messages": messages,
    }
    if stream:
        body["stream"] = True
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.functions:
        body["tools"] = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters_schema or {"type": "object", "properties": {}},
            }
            for t in req.functions
        ]
        body["tool_choice"] = _tool_choice(req.tool_choice)
    output_config: dict[str, Any] = {}
    if req.response_schema is not None:
        schema = normalize_json_schema(req.response_schema.model_json_schema())
        mode = opts.get("response_format", "json_schema")
        if mode == "json_schema":
            output_config["format"] = {"type": "json_schema", "schema": _strict_object_schema(schema)}
        elif mode != "prompt":
            raise InvalidRequestError(f"unknown response_format mode {mode!r}", provider=PROVIDER)
        # どの方式でもスキーマを指示文で添える（J-04）
        instruction = _schema_instruction(schema)
        system = f"{system}\n\n{instruction}" if system else instruction
    if effort := opts.get("effort"):
        output_config["effort"] = effort
    if output_config:
        body["output_config"] = output_config
    if system:
        body["system"] = system
    for key in _PASSTHROUGH:
        if key in opts:
            body[key] = opts[key]
    extra = opts.get("extra_body")
    if isinstance(extra, dict):
        body.update(extra)
    return body


# ---------------------------------------------------------------------------- 応答

_STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}


def map_finish(reason: str | None, has_tool_calls: bool) -> FinishReason:
    if reason is None:
        return "tool_calls" if has_tool_calls else "stop"
    return _STOP_REASONS.get(reason, "unknown")


def _usage(raw: Mapping[str, Any] | None) -> Usage:
    if not raw:
        return Usage()
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    cache_write = int(raw.get("cache_creation_input_tokens") or 0)
    prompt = int(raw.get("input_tokens") or 0) + cache_read + cache_write
    completion = int(raw.get("output_tokens") or 0)
    # 思考トークンの内訳は API が返さない（output_tokens に含まれる）。推測で分けない
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cache_read,
        total_tokens=prompt + completion,
    )


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


def _assemble(
    blocks: list[dict[str, Any]],
    stop_reason: str | None,
    usage: Usage,
    req: ChatRequest,
    request_id: str | None,
) -> ChatResponse:
    text: list[str] = []
    reasoning: list[str] = []
    calls: list[ToolCall] = []
    for b in blocks:
        kind = b.get("type")
        if kind == "text":
            text.append(b.get("text") or "")
        elif kind == "thinking":
            if b.get("thinking"):
                reasoning.append(b["thinking"])
        elif kind == "tool_use":
            calls.append(
                ToolCall(
                    id=b.get("id") or new_tool_call_id(),
                    name=b.get("name") or "",
                    arguments=_arguments(b.get("input")),
                )
            )
        # redacted_thinking・server_tool_use など知らないブロックは捨てずに provider_state へ残す
    content = "".join(text) or None
    parsed: Any = None
    if content and req.response_schema is not None:
        from ..._json_extract import parse_json_response

        try:
            parsed = parse_json_response(content)
        except Exception:
            parsed = None  # 本文の text はそのまま残す
    return ChatResponse(
        content=content,
        parsed=parsed,
        reasoning="".join(reasoning) or None,
        tool_calls=calls,
        finish_reason=map_finish(stop_reason, bool(calls)),
        # 応答の model ではなく、指定した名前を記録する（J-06）
        meta=ResponseMeta(provider=PROVIDER, model=req.model, usage=usage, request_id=request_id),
        provider_state={PROVIDER: {"content": blocks}} if blocks else {},
    )


def parse_response(data: Mapping[str, Any], req: ChatRequest) -> ChatResponse:
    if data.get("type") == "error":
        raise _error_event(data, req.model)
    blocks = [b for b in data.get("content") or [] if isinstance(b, dict)]
    return _assemble(blocks, data.get("stop_reason"), _usage(data.get("usage")), req, data.get("id"))


class AnthropicStreamState:
    """SSE を共通の StreamEvent に変換する。

    ``message_start`` に入力側の usage、``message_delta`` に出力側の usage が来るので合わせる。
    ツール入力は ``input_json_delta`` の断片で届き、``content_block_stop`` で組み立てる。
    """

    def __init__(self, req: ChatRequest) -> None:
        self.req = req
        self.decoder = SSEDecoder()
        self.blocks: dict[int, dict[str, Any]] = {}
        self.json_parts: dict[int, list[str]] = {}
        self.usage_raw: dict[str, Any] = {}
        self.stop_reason: str | None = None
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
            if self.stop_reason is None:
                raise ProviderOverloadedError(
                    "anthropic stream ended without message_stop",
                    provider=PROVIDER,
                    model=self.req.model,
                    retryable=False,
                )
            events.extend(self._complete())
        return events

    def _fail(self, err: OmniError) -> None:
        self._pending_error = err

    def _handle(self, sse_events: list[Any]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        for sse in sse_events:
            if self.completed:
                break
            try:
                obj = sse.json()
            except json.JSONDecodeError:
                self._fail(
                    ProviderOverloadedError(
                        f"malformed event in anthropic stream: {sse.data[:200]!r}",
                        provider=PROVIDER,
                        model=self.req.model,
                        retryable=False,
                    )
                )
                break
            if not isinstance(obj, dict):
                continue
            kind = obj.get("type")
            if kind == "error":
                # HTTP 200 のまま、ストリームの途中でエラーが届く
                self._fail(_error_event(obj, self.req.model))
                break
            if kind == "message_start":
                message = obj.get("message") or {}
                self.request_id = message.get("id")
                self.usage_raw.update(message.get("usage") or {})
            elif kind == "content_block_start":
                index = int(obj.get("index", len(self.blocks)))
                block = dict(obj.get("content_block") or {})
                self.blocks[index] = block
                if block.get("type") == "tool_use":
                    self.json_parts[index] = []
            elif kind == "content_block_delta":
                events.extend(self._delta(int(obj.get("index", 0)), obj.get("delta") or {}))
            elif kind == "content_block_stop":
                events.extend(self._stop_block(int(obj.get("index", 0))))
            elif kind == "message_delta":
                delta = obj.get("delta") or {}
                if delta.get("stop_reason"):
                    self.stop_reason = delta["stop_reason"]
                self.usage_raw.update({k: v for k, v in (obj.get("usage") or {}).items() if v is not None})
            elif kind == "message_stop":
                events.extend(self._complete())
                break
            # ping と知らないイベントは読み飛ばす
        return events

    def _delta(self, index: int, delta: Mapping[str, Any]) -> list[StreamEvent]:
        block = self.blocks.setdefault(index, {})
        kind = delta.get("type")
        if kind == "text_delta":
            text = delta.get("text") or ""
            block["text"] = block.get("text", "") + text
            return [StreamEvent(type="text_delta", delta_text=text)] if text else []
        if kind == "thinking_delta":
            text = delta.get("thinking") or ""
            block["thinking"] = block.get("thinking", "") + text
            return [StreamEvent(type="reasoning_delta", delta_text=text)] if text else []
        if kind == "signature_delta":
            block["signature"] = block.get("signature", "") + (delta.get("signature") or "")
        elif kind == "input_json_delta":
            self.json_parts.setdefault(index, []).append(delta.get("partial_json") or "")
        return []

    def _stop_block(self, index: int) -> list[StreamEvent]:
        block = self.blocks.get(index)
        if block is None or block.get("type") != "tool_use":
            return []
        block["input"] = _arguments("".join(self.json_parts.get(index, [])))
        tc = ToolCall(
            id=block.get("id") or new_tool_call_id(), name=block.get("name") or "", arguments=block["input"]
        )
        return [StreamEvent(type="tool_call", tool_call=tc)]

    def _complete(self) -> list[StreamEvent]:
        self.completed = True
        blocks = [self.blocks[i] for i in sorted(self.blocks)]
        for i, block in self.blocks.items():
            if block.get("type") == "tool_use" and "input" not in block:
                block["input"] = _arguments("".join(self.json_parts.get(i, [])))
        response = _assemble(blocks, self.stop_reason, _usage(self.usage_raw), self.req, self.request_id)
        return [
            StreamEvent(
                type="message_complete",
                finish_reason=response.finish_reason,
                response=response,
                meta=response.meta,
            )
        ]


# ---------------------------------------------------------------------------- エラー

_CONTEXT_HINTS = ("prompt is too long", "context window", "context length", "too many tokens")

# ストリーム途中の error イベントには HTTP ステータスが無い。type から決める
_ERROR_TYPE_STATUS = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "billing_error": 402,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "overloaded_error": 529,
}


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _error_event(data: Mapping[str, Any], model: str | None) -> OmniError:
    err = data.get("error")
    etype = str(err.get("type") or "") if isinstance(err, dict) else ""
    return map_error(_ERROR_TYPE_STATUS.get(etype, 500), json.dumps(data).encode(), {}, model)


def map_error(status: int, body: bytes, headers: Mapping[str, str], model: str | None) -> OmniError:
    data = parse_error_json(body)
    err = data.get("error") if isinstance(data, dict) else None
    etype = ""
    if isinstance(err, dict):
        message = err.get("message")
        etype = str(err.get("type") or "")
    else:
        message = err
    message = str(message or body[:500].decode("utf-8", errors="replace") or f"HTTP {status}")
    kw: dict[str, Any] = {"provider": PROVIDER, "model": model, "status_code": status}
    lowered = f"{message} {etype}".lower()
    if status in (400, 413, 422):
        if any(h in lowered for h in _CONTEXT_HINTS):
            return ContextLengthExceededError(message, **kw)
        if "not supported" in lowered or "not support" in lowered:
            return UnsupportedFeatureError(message, **kw)
        return InvalidRequestError(message, **kw)
    if status == 401:
        return AuthenticationError(message, **kw)
    if status == 402:
        return QuotaExceededError(message, **kw)
    if status == 403:
        return PermissionDeniedError(message, **kw)
    if status == 404:
        return NotFoundError(message, **kw)
    if status == 429:
        return RateLimitError(message, retry_after_seconds=_retry_after(headers), **kw)
    if status == 408 or status >= 500:  # 529 overloaded を含む
        return ProviderOverloadedError(message, **kw)
    return OmniError(message, **kw)
