"""GeminiChatAdapter の契約テスト — 実 API で記録できないケース（実装設計書 §6.3）。

⚠ このファイルの応答は一次資料の例から**手作り**したもの。実 API の記録を使うテストは
test_gemini_recorded.py にある。
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from omni_ai import (
    AuthenticationError,
    BuiltinTool,
    ChatMessage,
    ContextLengthExceededError,
    FileRef,
    InvalidRequestError,
    MediaPart,
    NotFoundError,
    OmniError,
    PermissionDeniedError,
    ProviderOverloadedError,
    QuotaExceededError,
    RateLimitError,
    SchemaValidationError,
    StreamInterruptedError,
    TextPart,
    ToolCall,
    UnsupportedFeatureError,
    UrlRef,
    omni_tool,
)
from omni_ai._http import RetryPolicy
from omni_ai.providers.gemini import GeminiChatAdapter
from omni_ai.providers.gemini.codec import DUMMY_THOUGHT_SIGNATURE

BASE = "https://gemini.test/v1beta"
MODEL = "gemini-3.1-flash-lite"
GEN = f"{BASE}/models/{MODEL}:generateContent"
STREAM = f"{BASE}/models/{MODEL}:streamGenerateContent"


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city"""
    return "sunny, 20C"


def adapter(**kw: Any) -> GeminiChatAdapter:
    params: dict[str, Any] = {
        "api_key": "test-key",
        "base_url": BASE,
        "retry": RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0),
    }
    params.update(kw)
    a = GeminiChatAdapter(**params)
    a._sync._sleep = lambda _s: None
    return a


def resp(parts: list[dict[str, Any]], finish: str = "STOP", **extra: Any) -> dict[str, Any]:
    cand: dict[str, Any] = {"content": {"role": "model", "parts": parts}, "finishReason": finish, "index": 0}
    cand.update(extra.pop("cand", {}))
    return {
        "candidates": [cand],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 7,
            "cachedContentTokenCount": 2,
            "totalTokenCount": 22,
        },
        "modelVersion": "gemini-3.8-flash-001",
        "responseId": "resp-123",
        **extra,
    }


def sse(*chunks: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\r\n\r\n" for c in chunks)


@respx.mock
def test_c01_text_usage_and_headers() -> None:
    route = respx.post(GEN).mock(return_value=httpx.Response(200, json=resp([{"text": "OK"}])))
    res = adapter().generate([ChatMessage.system("be brief"), ChatMessage.user("hi")])
    assert res.content == "OK" and res.finish_reason == "stop"
    assert res.meta.request_id == "resp-123"
    assert res.meta.model == "gemini-3.8-flash-001"  # 実際に応答したモデルを記録
    u = res.meta.usage
    assert (u.prompt_tokens, u.completion_tokens, u.reasoning_tokens, u.cached_tokens, u.total_tokens) == (
        10,
        5,
        7,
        2,
        22,
    )
    req = route.calls[0].request
    assert req.headers["x-goog-api-key"] == "test-key" and "key=" not in str(req.url)
    body = json.loads(req.content)
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]


SIG = "c2lnbmF0dXJl"
FC_PARTS = [
    {
        "functionCall": {"name": "get_weather", "args": {"city": "Tokyo"}, "id": "fc-1"},
        "thoughtSignature": SIG,
    },
    {"functionCall": {"name": "get_weather", "args": {"city": "Osaka"}, "id": "fc-2"}},
]


@respx.mock
def test_c02_c03_parallel_calls_signature_roundtrip() -> None:
    route = respx.post(GEN).mock(
        side_effect=[
            httpx.Response(200, json=resp(FC_PARTS)),
            httpx.Response(200, json=resp([{"text": "Both sunny."}])),
        ]
    )
    a = adapter()
    msgs = [ChatMessage.user("weather in Tokyo and Osaka?")]
    res = a.generate(msgs, tools=[get_weather])
    assert res.finish_reason == "tool_calls"
    assert [(t.id, t.arguments["city"]) for t in res.tool_calls] == [("fc-1", "Tokyo"), ("fc-2", "Osaka")]
    first = json.loads(route.calls[0].request.content)
    assert first["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "get_weather",
                    "description": "Get current weather for a city",
                    "parametersJsonSchema": get_weather.parameters_schema,
                }
            ]
        }
    ]
    assert first["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}

    history = [*msgs, res.to_message()]
    history += [
        ChatMessage.tool(get_weather(**t.arguments), tool_call_id=t.id, name=t.name) for t in res.tool_calls
    ]
    assert a.generate(history, tools=[get_weather]).content == "Both sunny."
    contents = json.loads(route.calls[1].request.content)["contents"]
    # model の parts は署名を含めてそのまま返る (ADR-11)
    assert contents[1] == {"role": "model", "parts": FC_PARTS}
    # FC1, FC2 の後に FR1, FR2 を 1 つの user Content で返す
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "get_weather", "response": {"result": "sunny, 20C"}, "id": "fc-1"}},
            {"functionResponse": {"name": "get_weather", "response": {"result": "sunny, 20C"}, "id": "fc-2"}},
        ],
    }
    assert len(contents) == 3


def test_foreign_assistant_history_gets_dummy_signature() -> None:
    from omni_ai.providers._base import ChatRequest
    from omni_ai.providers.gemini.codec import build_body

    msgs = [
        ChatMessage.user("q"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="call_x", name="f", arguments={"a": 1}),
                ToolCall(id="call_y", name="f", arguments={"a": 2}),
            ],
        ),
        ChatMessage.tool('{"ok": true}', tool_call_id="call_x", name="f"),
        ChatMessage.tool("2", tool_call_id="call_y", name="f"),
    ]
    body = build_body(ChatRequest(messages=msgs, model=MODEL), stream=False)
    parts = body["contents"][1]["parts"]
    assert parts[0]["thoughtSignature"] == DUMMY_THOUGHT_SIGNATURE and "thoughtSignature" not in parts[1]
    frs = body["contents"][2]["parts"]
    assert frs[0]["functionResponse"]["response"] == {"ok": True}  # JSON object はそのまま
    assert frs[1]["functionResponse"]["response"] == {"result": "2"}


def _sse_text() -> bytes:
    return sse(
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "1 2 "}]}, "index": 0}],
            "responseId": "r1",
            "modelVersion": "gemini-3.8-flash-001",
            "usageMetadata": {"promptTokenCount": 3, "totalTokenCount": 3},
        },
        {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": "3", "thoughtSignature": "s"}]}, "index": 0}
            ],
            "responseId": "r1",
        },
        {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": ""}]}, "finishReason": "STOP", "index": 0}
            ],
            "responseId": "r1",
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 3, "totalTokenCount": 6},
        },
    )


@respx.mock
def test_c04_c05_stream() -> None:
    route = respx.post(STREAM).mock(return_value=httpx.Response(200, content=_sse_text()))
    events = list(adapter().stream([ChatMessage.user("count")]))
    assert route.calls[0].request.url.params["alt"] == "sse"
    text = "".join(e.delta_text for e in events if e.type == "text_delta")
    assert text == "1 2 3"
    assert [e.type for e in events].count("message_complete") == 1 and events[-1].type == "message_complete"
    final = events[-1].response
    assert final is not None and final.content == text and final.finish_reason == "stop"
    assert final.meta.usage.total_tokens == 6 and final.meta.request_id == "r1"
    # 空 text に付いた署名も含め parts を保持する
    assert final.provider_state["gemini"]["content"]["parts"][1]["thoughtSignature"] == "s"


@respx.mock
async def test_async_stream_tool_call() -> None:
    respx.post(STREAM).mock(
        return_value=httpx.Response(
            200,
            content=sse(
                {
                    "candidates": [
                        {"content": {"role": "model", "parts": FC_PARTS}, "finishReason": "STOP", "index": 0}
                    ]
                }
            ),
        )
    )
    a = adapter()
    events = [e async for e in a.astream([ChatMessage.user("w")], tools=[get_weather])]
    assert [e.tool_call.id for e in events if e.type == "tool_call" and e.tool_call] == ["fc-1", "fc-2"]
    assert events[-1].finish_reason == "tool_calls"
    await a.aclose()


@respx.mock
def test_stream_error_event_after_output() -> None:
    respx.post(STREAM).mock(
        return_value=httpx.Response(
            200,
            content=sse(
                {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
                {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}},
            ),
        )
    )
    it = adapter().stream([ChatMessage.user("x")])
    assert next(it).delta_text == "Hel"
    with pytest.raises(StreamInterruptedError):
        next(it)


@respx.mock
def test_builtin_tools_and_citations() -> None:
    grounding = {
        "webSearchQueries": ["python latest"],
        "groundingChunks": [{"web": {"uri": "https://python.org", "title": "python.org"}}],
        "groundingSupports": [
            {"segment": {"startIndex": 0, "endIndex": 10, "text": "Python 3.x"}, "groundingChunkIndices": [0]}
        ],
    }
    route = respx.post(GEN).mock(
        return_value=httpx.Response(
            200, json=resp([{"text": "Python 3.x"}], cand={"groundingMetadata": grounding})
        )
    )
    res = adapter().generate([ChatMessage.user("latest python?")], tools=[BuiltinTool.WEB_SEARCH])
    assert res.citations[0].url == "https://python.org" and res.citations[0].end_index == 10
    body = json.loads(route.calls[0].request.content)
    assert body["tools"] == [{"google_search": {}}] and "toolConfig" not in body


def test_builtin_plus_function_uses_validated_mode() -> None:
    from omni_ai.providers._base import ChatRequest
    from omni_ai.providers.gemini.codec import build_body

    req = ChatRequest(
        messages=[ChatMessage.user("q")],
        model=MODEL,
        functions=[get_weather],
        builtins=[BuiltinTool.CODE_EXECUTION],
    )
    body = build_body(req, stream=False)
    assert body["tools"][0] == {"code_execution": {}}
    assert body["toolConfig"] == {
        "functionCallingConfig": {"mode": "VALIDATED"},
        "includeServerSideToolInvocations": True,
    }


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        ("none", {"mode": "NONE"}),
        ("required", {"mode": "ANY"}),
        ("get_weather", {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}),
    ],
)
def test_tool_choice(choice: str, expected: dict[str, Any]) -> None:
    from omni_ai.providers._base import ChatRequest
    from omni_ai.providers.gemini.codec import build_body

    body = build_body(
        ChatRequest(
            messages=[ChatMessage.user("q")], model=MODEL, functions=[get_weather], tool_choice=choice
        ),
        stream=False,
    )
    assert body["toolConfig"]["functionCallingConfig"] == expected


def _err(code: int, status: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "status": status, "message": message, "details": details or []}}


@pytest.mark.parametrize(
    ("status", "payload", "exc", "calls"),
    [
        (
            400,
            _err(
                400,
                "INVALID_ARGUMENT",
                "API key not valid. Please pass a valid API key.",
                [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}],
            ),
            AuthenticationError,
            1,
        ),
        (400, _err(400, "FAILED_PRECONDITION", "User location is not supported"), PermissionDeniedError, 1),
        (400, _err(400, "INVALID_ARGUMENT", "bad role"), InvalidRequestError, 1),
        (402, _err(402, "RESOURCE_EXHAUSTED", "prepay balance is zero"), QuotaExceededError, 1),
        (403, _err(403, "PERMISSION_DENIED", "denied"), PermissionDeniedError, 1),
        (404, _err(404, "NOT_FOUND", "models/x is not found"), NotFoundError, 1),
        (429, _err(429, "RESOURCE_EXHAUSTED", "quota"), RateLimitError, 2),
        (500, _err(500, "INTERNAL", "Your input context is too long"), ContextLengthExceededError, 1),
        (503, _err(503, "UNAVAILABLE", "overloaded"), ProviderOverloadedError, 2),
    ],
)
@respx.mock
def test_c06_c07_error_mapping(
    status: int, payload: dict[str, Any], exc: type[OmniError], calls: int
) -> None:
    route = respx.post(GEN).mock(return_value=httpx.Response(status, json=payload))
    with pytest.raises(exc) as ei:
        adapter().generate([ChatMessage.user("hi")])
    assert route.call_count == calls
    assert ei.value.model == MODEL
    assert "test-key" not in json.dumps(ei.value.raw_error, default=str)


@respx.mock
def test_429_retry_info_delay() -> None:
    delays: list[float] = []
    route = respx.post(GEN).mock(
        side_effect=[
            httpx.Response(
                429,
                json=_err(
                    429,
                    "RESOURCE_EXHAUSTED",
                    "q",
                    [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3s"}],
                ),
            ),
            httpx.Response(200, json=resp([{"text": "ok"}])),
        ]
    )
    a = adapter(retry=RetryPolicy(max_attempts=2, max_delay=5.0))
    a._sync._sleep = delays.append
    assert a.generate([ChatMessage.user("hi")]).meta.attempts == 2
    assert delays == [3.0] and route.call_count == 2


class Person(BaseModel):
    name: str
    age: int


@respx.mock
def test_c08_schema() -> None:
    route = respx.post(GEN).mock(
        side_effect=[
            httpx.Response(200, json=resp([{"text": '{"name": "Ann", "age": 30}'}])),
            httpx.Response(200, json=resp([{"text": "not json"}])),
        ]
    )
    a = adapter()
    assert a.generate([ChatMessage.user("p")], response_schema=Person).parsed == Person(name="Ann", age=30)
    gen = json.loads(route.calls[0].request.content)["generationConfig"]
    assert gen["responseMimeType"] == "application/json" and gen["responseJsonSchema"]["required"] == [
        "name",
        "age",
    ]
    with pytest.raises(SchemaValidationError):
        a.generate([ChatMessage.user("p")], response_schema=Person)


def test_c09_closed() -> None:
    a = adapter()
    a.close()
    with pytest.raises(OmniError, match="closed"):
        a.generate([ChatMessage.user("x")])


@respx.mock
def test_blocked_prompt_and_finish_mapping() -> None:
    respx.post(GEN).mock(
        side_effect=[
            httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
            httpx.Response(200, json=resp([{"text": "abc"}], finish="MAX_TOKENS")),
            httpx.Response(200, json=resp([{"text": "x"}], finish="SOME_NEW_REASON")),
        ]
    )
    a = adapter()
    blocked = a.generate([ChatMessage.user("x")])
    assert blocked.content is None and blocked.finish_reason == "content_filter"
    assert a.generate([ChatMessage.user("x")], max_tokens=3).finish_reason == "length"
    assert a.generate([ChatMessage.user("x")]).finish_reason == "unknown"


@respx.mock
def test_media_parts_and_options(tmp_path: Path) -> None:
    route = respx.post(GEN).mock(return_value=httpx.Response(200, json=resp([{"text": "ok"}])))
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    msg = ChatMessage.user(
        [
            TextPart(text="describe"),
            MediaPart(type="image", source=img, mime_type="image/png"),
            MediaPart(type="document", source=UrlRef(url="https://x/doc.pdf"), mime_type="application/pdf"),
            MediaPart(
                type="video",
                source=FileRef(
                    provider="gemini",
                    file_id="files/abc",
                    uri="https://gemini.test/v1beta/files/abc",
                    mime_type="video/mp4",
                ),
                mime_type="video/mp4",
            ),
        ]
    )
    adapter().generate(
        [msg],
        temperature=0.3,
        max_tokens=50,
        gemini={"thinking_level": "low", "include_thoughts": True},
        ollama={"num_ctx": 1},
    )
    body = json.loads(route.calls[0].request.content)
    parts = body["contents"][0]["parts"]
    assert parts[1] == {"inlineData": {"mimeType": "image/png", "data": "iVBORw=="}}
    assert parts[2] == {"fileData": {"fileUri": "https://x/doc.pdf", "mimeType": "application/pdf"}}
    assert parts[3]["fileData"]["fileUri"] == "https://gemini.test/v1beta/files/abc"
    assert body["generationConfig"] == {
        "temperature": 0.3,
        "maxOutputTokens": 50,
        "thinkingConfig": {"thinkingLevel": "LOW", "includeThoughts": True},
    }
    with pytest.raises(UnsupportedFeatureError):
        adapter().generate(
            [
                ChatMessage.user(
                    [
                        MediaPart(
                            type="image",
                            source=FileRef(provider="openai", file_id="f", mime_type="image/png"),
                            mime_type="image/png",
                        )
                    ]
                )
            ]
        )


@respx.mock
def test_large_media_uploaded_via_files_api(tmp_path: Path) -> None:
    big = tmp_path / "big.pdf"
    big.write_bytes(b"%PDF" + b"0" * 2000)
    start = respx.post("https://gemini.test/upload/v1beta/files").mock(
        return_value=httpx.Response(
            200, headers={"x-goog-upload-url": "https://gemini.test/session/1"}, json={}
        )
    )
    finalize = respx.post("https://gemini.test/session/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "file": {
                    "name": "files/f1",
                    "uri": "https://gemini.test/v1beta/files/f1",
                    "mimeType": "application/pdf",
                    "state": "PROCESSING",
                }
            },
        )
    )
    poll = respx.get("https://gemini.test/v1beta/files/f1").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "files/f1",
                "uri": "https://gemini.test/v1beta/files/f1",
                "mimeType": "application/pdf",
                "state": "ACTIVE",
            },
        )
    )
    gen = respx.post(GEN).mock(return_value=httpx.Response(200, json=resp([{"text": "ok"}])))
    a = adapter(inline_limit=1000)
    msg = ChatMessage.user([MediaPart(type="document", source=big, mime_type="application/pdf")])
    a.generate([msg])
    assert start.calls[0].request.headers["x-goog-upload-header-content-length"] == "2004"
    assert finalize.calls[0].request.headers["x-goog-upload-command"] == "upload, finalize"
    assert finalize.calls[0].request.content == big.read_bytes()
    assert poll.call_count == 1
    part = json.loads(gen.calls[0].request.content)["contents"][0]["parts"][0]
    assert part == {
        "fileData": {"fileUri": "https://gemini.test/v1beta/files/f1", "mimeType": "application/pdf"}
    }
    assert isinstance(msg.content, list) and isinstance(msg.content[0], MediaPart)
    assert msg.content[0].source == big  # 呼び出し側のメッセージは書き換えない


@respx.mock
async def test_async_upload_streams_file(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 3000)
    respx.post("https://gemini.test/upload/v1beta/files").mock(
        return_value=httpx.Response(200, headers={"x-goog-upload-url": "https://gemini.test/s/2"}, json={})
    )
    fin = respx.post("https://gemini.test/s/2").mock(
        return_value=httpx.Response(
            200,
            json={
                "file": {
                    "name": "files/f2",
                    "uri": "u",
                    "mimeType": "application/octet-stream",
                    "state": "ACTIVE",
                }
            },
        )
    )
    ref = await adapter().aupload_file(big, "application/octet-stream")
    assert ref == FileRef(
        provider="gemini", file_id="files/f2", uri="u", mime_type="application/octet-stream"
    )
    assert fin.calls[0].request.headers["content-length"] == "3000"


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OMNI_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("omni_ai.config.load_config_file", lambda path=None: {})
    with pytest.raises(AuthenticationError, match="API key not found"):
        GeminiChatAdapter()
