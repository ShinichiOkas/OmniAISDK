"""共通型 ⇔ Gemini generateContent の JSON（I/O を持たない）。実装設計書 §7。"""

from __future__ import annotations

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
    BuiltinTool,
    ChatMessage,
    ChatResponse,
    Citation,
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

PROVIDER = "gemini"
DEFAULT_INLINE_LIMIT = 20 * 1024 * 1024
# 他社由来・手組みの履歴に付ける公式のダミー署名 [確認済]
DUMMY_THOUGHT_SIGNATURE = "skip_thought_signature_validator"

__all__ = [
    "DUMMY_THOUGHT_SIGNATURE",
    "GeminiStreamState",
    "build_body",
    "endpoint",
    "map_error",
    "parse_response",
]


def model_path(model: str) -> str:
    return model if model.startswith(("models/", "tunedModels/")) else f"models/{model}"


def endpoint(req: ChatRequest, *, stream: bool) -> str:
    if stream:
        return f"/{model_path(req.model)}:streamGenerateContent?alt=sse"
    return f"/{model_path(req.model)}:generateContent"


# ---------------------------------------------------------------------------- 要求


def _media_part(part: MediaPart, inline_limit: int) -> dict[str, Any]:
    src = part.source
    if isinstance(src, FileRef):
        if src.provider != PROVIDER:
            raise UnsupportedFeatureError(
                f"FileRef from {src.provider!r} cannot be sent to Gemini", provider=PROVIDER
            )
        return {"fileData": {"fileUri": src.uri or src.file_id, "mimeType": src.mime_type or part.mime_type}}
    if isinstance(src, UrlRef):
        return {"fileData": {"fileUri": src.url, "mimeType": part.mime_type}}
    return {
        "inlineData": {
            "mimeType": part.mime_type,
            "data": to_base64(src, limit=inline_limit, provider=PROVIDER),
        }
    }


def _user_parts(msg: ChatMessage, inline_limit: int) -> list[dict[str, Any]]:
    if isinstance(msg.content, str):
        return [{"text": msg.content}] if msg.content else []
    parts: list[dict[str, Any]] = []
    for p in msg.content:
        if isinstance(p, TextPart):
            parts.append({"text": p.text})
        elif isinstance(p, MediaPart):
            parts.append(_media_part(p, inline_limit))
    return parts


def _assistant_content(msg: ChatMessage, inline_limit: int) -> dict[str, Any]:
    stored = msg.provider_state.get(PROVIDER, {}).get("content")
    if isinstance(stored, dict) and stored.get("parts"):
        # thought signature 等を欠かさないため、モデル応答の parts をそのまま返す (ADR-11)
        return {"role": "model", "parts": stored["parts"]}
    parts = _user_parts(msg, inline_limit)
    for i, tc in enumerate(msg.tool_calls or []):
        fc: dict[str, Any] = {"name": tc.name, "args": tc.arguments, "id": tc.id}
        part: dict[str, Any] = {"functionCall": fc}
        if i == 0:
            part["thoughtSignature"] = DUMMY_THOUGHT_SIGNATURE
        parts.append(part)
    return {"role": "model", "parts": parts}


def _function_response(msg: ChatMessage) -> dict[str, Any]:
    if not msg.name:
        raise InvalidRequestError("tool message for Gemini requires `name`", provider=PROVIDER)
    text = msg.text()
    response: Any
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    response = decoded if isinstance(decoded, dict) else {"result": text}
    fr: dict[str, Any] = {"name": msg.name, "response": response}
    if msg.tool_call_id:
        fr["id"] = msg.tool_call_id
    return {"functionResponse": fr}


def build_contents(messages: list[ChatMessage], inline_limit: int) -> tuple[dict[str, Any] | None, list[Any]]:
    system_texts: list[str] = []
    contents: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role is Role.SYSTEM:
            if not isinstance(msg.content, str) and any(isinstance(p, MediaPart) for p in msg.content):
                raise UnsupportedFeatureError("Gemini systemInstruction accepts text only", provider=PROVIDER)
            system_texts.append(msg.text())
        elif msg.role is Role.USER:
            contents.append({"role": "user", "parts": _user_parts(msg, inline_limit)})
        elif msg.role is Role.ASSISTANT:
            contents.append(_assistant_content(msg, inline_limit))
        elif msg.role is Role.TOOL:
            part = _function_response(msg)
            last = contents[-1] if contents else None
            # 連続するツール結果は 1 つの user Content にまとめる（FC1, FC2, FR1, FR2 の順）
            if last is not None and last["role"] == "user" and last.get("_tool_results"):
                last["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part], "_tool_results": True})
    for c in contents:
        c.pop("_tool_results", None)
    # 最後が model のターン（prefill）は送信前に拒否しない。資料では Gemini 3.6+ が 400 を返すが、
    # 実測で gemini-3.1-flash-lite は受け付けた（2026-11-07）。可否はモデルに任せ、400 は例外に写像する
    system = {"parts": [{"text": "\n".join(system_texts)}]} if system_texts else None
    return system, contents


_BUILTIN_WIRE: dict[BuiltinTool, dict[str, Any]] = {
    BuiltinTool.WEB_SEARCH: {"google_search": {}},
    BuiltinTool.CODE_EXECUTION: {"code_execution": {}},
}


def build_body(req: ChatRequest, *, stream: bool) -> dict[str, Any]:
    opts = req.options
    inline_limit = int(opts.get("inline_limit", DEFAULT_INLINE_LIMIT))
    system, contents = build_contents(req.messages, inline_limit)
    body: dict[str, Any] = {"contents": contents}
    if system:
        body["systemInstruction"] = system

    gen: dict[str, Any] = dict(opts.get("generation_config", {}))
    if req.temperature is not None:
        gen["temperature"] = req.temperature
    if req.max_tokens is not None:
        gen["maxOutputTokens"] = req.max_tokens
    if req.response_schema is not None:
        gen["responseMimeType"] = "application/json"
        gen["responseJsonSchema"] = normalize_json_schema(req.response_schema.model_json_schema())
    thinking: dict[str, Any] = {}
    if "thinking_level" in opts:
        thinking["thinkingLevel"] = str(opts["thinking_level"]).upper()
    if "thinking_budget" in opts:
        thinking["thinkingBudget"] = opts["thinking_budget"]
    if opts.get("include_thoughts"):
        thinking["includeThoughts"] = True
    if thinking:
        gen["thinkingConfig"] = thinking
    if gen:
        body["generationConfig"] = gen

    tools: list[dict[str, Any]] = [_BUILTIN_WIRE[b] for b in req.builtins]
    use_functions = bool(req.functions) and req.tool_choice != "none"
    if req.functions:
        tools.append(
            {
                "functionDeclarations": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "parametersJsonSchema": t.parameters_schema,
                    }
                    for t in req.functions
                ]
            }
        )
    if tools:
        body["tools"] = tools
    if req.functions:
        fcc: dict[str, Any]
        if not use_functions:
            fcc = {"mode": "NONE"}
        elif req.tool_choice == "required":
            fcc = {"mode": "ANY"}
        elif req.tool_choice != "auto":
            fcc = {"mode": "ANY", "allowedFunctionNames": [req.tool_choice]}
        elif req.builtins:
            fcc = {"mode": "VALIDATED"}  # 組み込みとの併用時は AUTO 不可 [確認済]
        else:
            fcc = {"mode": "AUTO"}
        tool_config: dict[str, Any] = {"functionCallingConfig": fcc}
        if req.builtins:
            tool_config["includeServerSideToolInvocations"] = True  # Preview 機能
        body["toolConfig"] = tool_config
    if "safety_settings" in opts:
        body["safetySettings"] = opts["safety_settings"]
    if "cached_content" in opts:
        body["cachedContent"] = opts["cached_content"]
    return body


# ---------------------------------------------------------------------------- 応答

_FILTER = {
    "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
    "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION",
}  # fmt: skip
_ERROR = {
    "MALFORMED_FUNCTION_CALL", "UNEXPECTED_TOOL_CALL", "TOO_MANY_TOOL_CALLS",
    "MISSING_THOUGHT_SIGNATURE", "MALFORMED_RESPONSE",
}  # fmt: skip


def map_finish(reason: str | None, has_calls: bool) -> FinishReason:
    if reason == "STOP":
        return "tool_calls" if has_calls else "stop"
    if reason in ("MAX_TOKENS", "CONTINUATION"):
        return "length"
    if reason in _FILTER:
        return "content_filter"
    if reason in _ERROR:
        return "error"
    if reason is None and has_calls:
        return "tool_calls"
    return "unknown"


def _usage(meta: Mapping[str, Any] | None) -> Usage:
    m = meta or {}
    prompt = int(m.get("promptTokenCount") or 0)
    completion = int(m.get("candidatesTokenCount") or 0)
    reasoning = int(m.get("thoughtsTokenCount") or 0)
    total = int(m.get("totalTokenCount") or (prompt + completion + reasoning))
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=int(m.get("cachedContentTokenCount") or 0),
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


def _citations(grounding: Mapping[str, Any] | None) -> list[Citation]:
    if not grounding:
        return []
    chunks = grounding.get("groundingChunks") or []
    out: list[Citation] = []
    supports = grounding.get("groundingSupports") or []
    for sup in supports:
        seg = sup.get("segment") or {}
        for idx in sup.get("groundingChunkIndices") or []:
            if 0 <= idx < len(chunks) and (web := chunks[idx].get("web")) and web.get("uri"):
                out.append(
                    Citation(
                        url=web["uri"],
                        title=web.get("title"),
                        start_index=seg.get("startIndex"),
                        end_index=seg.get("endIndex"),
                    )
                )
    if not out:  # supports が無ければ出典の一覧だけを返す
        out = [
            Citation(url=c["web"]["uri"], title=c["web"].get("title"))
            for c in chunks
            if c.get("web", {}).get("uri")
        ]
    return out


def _tool_call(fc: Mapping[str, Any]) -> ToolCall:
    args = fc.get("args") or {}
    return ToolCall(
        id=fc.get("id") or new_tool_call_id(),
        name=fc.get("name", ""),
        arguments=args if isinstance(args, dict) else {"_raw": args},
    )


def _split_parts(parts: list[Mapping[str, Any]]) -> tuple[str, str, list[ToolCall]]:
    text: list[str] = []
    thoughts: list[str] = []
    calls: list[ToolCall] = []
    for p in parts:
        if "functionCall" in p:
            calls.append(_tool_call(p["functionCall"]))
        elif "text" in p:
            (thoughts if p.get("thought") else text).append(p["text"])
    return "".join(text), "".join(thoughts), calls


def parse_response(data: Mapping[str, Any], req: ChatRequest) -> ChatResponse:
    meta = ResponseMeta(
        provider=PROVIDER,
        model=data.get("modelVersion") or req.model,
        request_id=data.get("responseId"),
        usage=_usage(data.get("usageMetadata")),
    )
    candidates = data.get("candidates") or []
    if not candidates:
        return ChatResponse(
            content=None,
            finish_reason="content_filter" if data.get("promptFeedback") else "unknown",
            meta=meta,
        )
    cand = candidates[0]
    content = cand.get("content") or {}
    parts = content.get("parts") or []
    text, thoughts, calls = _split_parts(parts)
    return ChatResponse(
        content=text or None,
        reasoning=thoughts or None,
        tool_calls=calls,
        citations=_citations(cand.get("groundingMetadata")),
        finish_reason=map_finish(cand.get("finishReason"), bool(calls)),
        meta=meta,
        provider_state={PROVIDER: {"content": {"role": "model", "parts": parts}}} if parts else {},
    )


class GeminiStreamState:
    """SSE（alt=sse）を StreamEvent に変換する。各イベントは GenerateContentResponse と同形。"""

    def __init__(self, req: ChatRequest) -> None:
        self.req = req
        self.decoder = SSEDecoder()
        self.parts: list[dict[str, Any]] = []
        self.calls: list[ToolCall] = []
        self.text: list[str] = []
        self.thoughts: list[str] = []
        self.finish_reason: str | None = None
        self.usage: Mapping[str, Any] | None = None
        self.grounding: Mapping[str, Any] | None = None
        self.response_id: str | None = None
        self.model_version: str | None = None
        self.blocked = False
        self._pending_error: OmniError | None = None

    def feed(self, chunk: bytes) -> list[StreamEvent]:
        self._raise_pending()
        events = self._handle([e.data for e in self.decoder.feed(chunk)])
        if not events:
            self._raise_pending()
        return events

    def finish(self) -> list[StreamEvent]:
        self._raise_pending()
        events = self._handle([e.data for e in self.decoder.flush()])
        self._raise_pending()
        if self.finish_reason is None and not self.blocked:
            raise ProviderOverloadedError(
                "Gemini stream ended without finishReason",
                provider=PROVIDER,
                model=self.req.model,
                retryable=False,
            )
        has_calls = bool(self.calls)
        finish = "content_filter" if self.blocked else map_finish(self.finish_reason, has_calls)
        response = ChatResponse(
            content="".join(self.text) or None,
            reasoning="".join(self.thoughts) or None,
            tool_calls=list(self.calls),
            citations=_citations(self.grounding),
            finish_reason=finish,
            meta=ResponseMeta(
                provider=PROVIDER,
                model=self.model_version or self.req.model,
                request_id=self.response_id,
                usage=_usage(self.usage),
            ),
            provider_state={PROVIDER: {"content": {"role": "model", "parts": self.parts}}}
            if self.parts
            else {},
        )
        for c in response.citations:
            events.append(StreamEvent(type="citation", citation=c))
        events.append(
            StreamEvent(type="message_complete", finish_reason=finish, response=response, meta=response.meta)
        )
        return events

    def _raise_pending(self) -> None:
        if self._pending_error is not None:
            raise self._pending_error

    def _handle(self, payloads: list[str]) -> list[StreamEvent]:
        events: list[StreamEvent] = []
        for raw in payloads:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._pending_error = ProviderOverloadedError(
                    f"malformed event in Gemini stream: {raw[:200]!r}",
                    provider=PROVIDER,
                    model=self.req.model,
                    retryable=False,
                )
                break
            if not isinstance(data, dict):
                continue
            if "error" in data:  # [未実測] ストリーム途中のエラーの形
                err = data["error"]
                status = int(err.get("code") or 500) if isinstance(err, dict) else 500
                self._pending_error = map_error(status, raw.encode(), {}, self.req.model)
                break
            self.response_id = data.get("responseId") or self.response_id
            self.model_version = data.get("modelVersion") or self.model_version
            if data.get("usageMetadata"):
                self.usage = data["usageMetadata"]  # 最後に受け取った値を採用
            cands = data.get("candidates") or []
            if not cands:
                if data.get("promptFeedback", {}).get("blockReason"):
                    self.blocked = True
                continue
            cand = cands[0]
            for p in (cand.get("content") or {}).get("parts") or []:
                self.parts.append(p)
                if "functionCall" in p:
                    tc = _tool_call(p["functionCall"])
                    self.calls.append(tc)
                    events.append(StreamEvent(type="tool_call", tool_call=tc))
                elif p.get("text"):
                    if p.get("thought"):
                        self.thoughts.append(p["text"])
                        events.append(StreamEvent(type="reasoning_delta", delta_text=p["text"]))
                    else:
                        self.text.append(p["text"])
                        events.append(StreamEvent(type="text_delta", delta_text=p["text"]))
            if cand.get("groundingMetadata"):
                self.grounding = cand["groundingMetadata"]
            if cand.get("finishReason"):
                self.finish_reason = cand["finishReason"]
        return events


# ---------------------------------------------------------------------------- エラー

_DURATION = re.compile(r"^\s*([0-9.]+)s\s*$")
_CONTEXT_HINTS = (
    "context is too long",
    "too many tokens",
    "exceeds the maximum number of tokens",
    "input token count",
    "context length",
)


def _retry_delay(details: list[Any], headers: Mapping[str, str]) -> float | None:
    for d in details:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
            m = _DURATION.match(str(d.get("retryDelay", "")))
            if m:
                return float(m.group(1))
    ra = headers.get("retry-after")
    try:
        return float(ra) if ra else None
    except ValueError:
        return None


def map_error(status: int, body: bytes, headers: Mapping[str, str], model: str | None) -> OmniError:
    data = parse_error_json(body)
    err = data.get("error") if isinstance(data, dict) else None
    err = err if isinstance(err, dict) else {}
    message = str(err.get("message") or body[:500].decode("utf-8", errors="replace") or f"HTTP {status}")
    grpc_status = str(err.get("status") or "")
    raw_details = err.get("details")
    details: list[Any] = raw_details if isinstance(raw_details, list) else []
    reasons = {str(d.get("reason")) for d in details if isinstance(d, dict) and d.get("reason")}
    kw: dict[str, Any] = {"provider": PROVIDER, "model": model, "status_code": status}
    lowered = message.lower()
    # 402 と 429 はどちらも RESOURCE_EXHAUSTED → HTTP ステータスで判定する [確認済]
    if status == 400:
        if "API_KEY_INVALID" in reasons or "api key not valid" in lowered:
            return AuthenticationError(message, **kw)
        if grpc_status == "FAILED_PRECONDITION":
            return PermissionDeniedError(message, **kw)
        if any(h in lowered for h in _CONTEXT_HINTS):  # [未検証] 文言は実測で固める
            return ContextLengthExceededError(message, **kw)
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
        return RateLimitError(message, retry_after_seconds=_retry_delay(details, headers), **kw)
    if status >= 500:
        if any(h in lowered for h in _CONTEXT_HINTS):
            return ContextLengthExceededError(message, **kw)
        return ProviderOverloadedError(message, **kw)
    return OmniError(message, **kw)
