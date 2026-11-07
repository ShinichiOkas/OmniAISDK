"""OllamaChatAdapter の契約テスト（実装設計書 §6.3 C-01〜09）。応答は実 API の記録（fixtures/ollama）。"""

import json
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from omni_ai import (
    BuiltinTool,
    ChatMessage,
    NotFoundError,
    OmniError,
    ProviderConnectionError,
    ProviderOverloadedError,
    SchemaValidationError,
    StreamInterruptedError,
    UnsupportedFeatureError,
    omni_tool,
)
from omni_ai._http import RetryPolicy
from omni_ai.providers.ollama import OllamaChatAdapter

BASE = "http://ollama.test:11434"
MODEL = "gemma4:31b-cloud"


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city"""
    return "sunny, 20C"


def adapter(**kw: object) -> OllamaChatAdapter:
    params: dict[str, object] = {
        "base_url": BASE,
        "default_model": MODEL,
        "retry": RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0),
    }
    params.update(kw)
    a = OllamaChatAdapter(**params)  # type: ignore[arg-type]
    a._sync._sleep = lambda _s: None
    return a


SHOW_TOOLS = {"capabilities": ["completion", "thinking", "tools", "vision"]}


def mock_show(caps: dict[str, object] = SHOW_TOOLS) -> respx.Route:
    return respx.post(f"{BASE}/api/show").mock(return_value=httpx.Response(200, json=caps))


@respx.mock
def test_c01_text(fixture_bytes) -> None:
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama", "text.json"))
    )
    res = adapter().generate([ChatMessage.user("Reply with exactly: OK")])
    assert res.content == "OK"
    assert res.finish_reason == "stop"
    assert res.meta.provider == "ollama" and res.meta.model == MODEL
    assert (res.meta.usage.prompt_tokens, res.meta.usage.completion_tokens, res.meta.usage.total_tokens) == (
        18,
        2,
        20,
    )
    body = json.loads(route.calls[0].request.content)
    assert body["stream"] is False and body["messages"] == [
        {"role": "user", "content": "Reply with exactly: OK"}
    ]


@respx.mock
def test_c02_c03_tool_calls_and_roundtrip(fixture_bytes) -> None:
    mock_show()
    chat = respx.post(f"{BASE}/api/chat").mock(
        side_effect=[
            httpx.Response(200, content=fixture_bytes("ollama", "tools.json")),
            httpx.Response(200, content=fixture_bytes("ollama", "tools_followup.json")),
        ]
    )
    a = adapter()
    msgs = [ChatMessage.user("What's the weather in Tokyo and in Osaka? Use the tool for each city.")]
    res = a.generate(msgs, tools=[get_weather])
    assert res.finish_reason == "tool_calls"
    assert [(tc.id, tc.name, tc.arguments) for tc in res.tool_calls] == [
        ("call_ch2yu7hq", "get_weather", {"city": "Tokyo"}),
        ("call_6g0ckg56", "get_weather", {"city": "Osaka"}),
    ]
    sent = json.loads(chat.calls[0].request.content)
    assert sent["tools"][0]["function"]["name"] == "get_weather"
    assert sent["tools"][0]["function"]["parameters"]["required"] == ["city"]

    history = [*msgs, res.to_message()]
    history += [
        ChatMessage.tool(get_weather(**tc.arguments), tool_call_id=tc.id, name=tc.name)
        for tc in res.tool_calls
    ]
    final = a.generate(history, tools=[get_weather])
    assert final.content and "20" in final.content

    wire = json.loads(chat.calls[1].request.content)["messages"]
    expected = json.loads(fixture_bytes("ollama", "tools_followup.request.json"))["messages"]
    assert wire[1]["tool_calls"] == [
        {"type": "function", "id": c["id"], "function": {**c["function"]}} for c in expected[1]["tool_calls"]
    ]
    assert [(m["role"], m["tool_name"], m["tool_call_id"]) for m in wire[2:]] == [
        (m["role"], m["tool_name"], m["tool_call_id"]) for m in expected[2:]
    ]


def _collect(events: list) -> tuple[str, list, list]:
    text = "".join(e.delta_text for e in events if e.type == "text_delta")
    calls = [e.tool_call for e in events if e.type == "tool_call"]
    completes = [e for e in events if e.type == "message_complete"]
    return text, calls, completes


@respx.mock
def test_c04_c05_stream_matches_nonstream(fixture_bytes) -> None:
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama", "text_stream.ndjson"))
    )
    events = list(adapter().stream([ChatMessage.user("Count")]))
    text, _, completes = _collect(events)
    assert text.split() == ["1", "2", "3", "4", "5"]
    assert len(completes) == 1 and events[-1] is completes[0]
    final = completes[0].response
    assert final is not None and final.content == text and final.finish_reason == "stop"
    assert final.meta.usage.completion_tokens == 10


@respx.mock
def test_stream_tool_calls(fixture_bytes) -> None:
    mock_show()
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama", "tools_stream.ndjson"))
    )
    events = list(adapter().stream([ChatMessage.user("weather")], tools=[get_weather]))
    _, calls, completes = _collect(events)
    assert [c.arguments["city"] for c in calls] == ["Tokyo", "Osaka"]
    assert completes[0].finish_reason == "tool_calls"
    assert completes[0].response is not None and len(completes[0].response.tool_calls) == 2


@respx.mock
def test_stream_thinking(fixture_bytes) -> None:
    mock_show()
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama", "thinking_stream.ndjson"))
    )
    events = list(adapter().stream([ChatMessage.user("Is 17 prime?")], ollama={"think": True}))
    assert any(e.type == "reasoning_delta" for e in events)
    final = events[-1].response
    assert final is not None and final.reasoning and final.content and final.content.startswith("Yes")


@respx.mock
async def test_async_paths(fixture_bytes) -> None:
    respx.post(f"{BASE}/api/chat").mock(
        side_effect=[
            httpx.Response(200, content=fixture_bytes("ollama", "text.json")),
            httpx.Response(200, content=fixture_bytes("ollama", "text_stream.ndjson")),
        ]
    )
    a = adapter()
    res = await a.agenerate([ChatMessage.user("hi")])
    assert res.content == "OK"
    events = [e async for e in a.astream([ChatMessage.user("count")])]
    assert events[-1].type == "message_complete"
    await a.aclose()


@pytest.mark.parametrize(
    ("status", "body", "exc", "retryable"),
    [
        (404, {"error": "model 'no-such-model:1b' not found"}, NotFoundError, False),  # 実測
        (
            500,
            {"error": "mlx runner failed: Error: unsupported architecture: X"},
            ProviderOverloadedError,
            False,
        ),  # 実測
        (503, {"error": "server busy"}, ProviderOverloadedError, True),
        (400, {"error": '"x" does not support tools'}, UnsupportedFeatureError, False),
    ],
)
@respx.mock
def test_c06_error_mapping(status: int, body: dict, exc: type[OmniError], retryable: bool) -> None:
    route = respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(status, json=body))
    with pytest.raises(exc) as ei:
        adapter().generate([ChatMessage.user("hi")])
    assert ei.value.retryable is retryable
    assert ei.value.model == MODEL and ei.value.status_code == status
    assert route.call_count == (2 if retryable else 1)


@respx.mock
def test_c06_connection_refused() -> None:
    respx.post(f"{BASE}/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderConnectionError):
        adapter().generate([ChatMessage.user("hi")])


@respx.mock
def test_c07_retry_then_success(fixture_bytes) -> None:
    respx.post(f"{BASE}/api/chat").mock(
        side_effect=[
            httpx.Response(503, json={"error": "busy"}),
            httpx.Response(200, content=fixture_bytes("ollama", "text.json")),
        ]
    )
    assert adapter().generate([ChatMessage.user("hi")]).meta.attempts == 2


class Person(BaseModel):
    name: str
    age: int


@respx.mock
def test_c08_response_schema_with_code_fence(fixture_bytes) -> None:
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama", "schema.json"))
    )
    res = adapter().generate([ChatMessage.user("Ann 30")], response_schema=Person)
    assert res.parsed == Person(name="Ann", age=30)
    assert json.loads(route.calls[0].request.content)["format"]["required"] == ["name", "age"]


@respx.mock
def test_c08_schema_validation_error() -> None:
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": MODEL,
                "message": {"role": "assistant", "content": "nope"},
                "done": True,
                "done_reason": "stop",
            },
        )
    )
    with pytest.raises(SchemaValidationError):
        adapter().generate([ChatMessage.user("x")], response_schema=Person)


def test_c09_closed_adapter_raises() -> None:
    a = adapter()
    a.close()
    with pytest.raises(OmniError, match="closed"):
        a.generate([ChatMessage.user("hi")])


@respx.mock
def test_length_finish(fixture_bytes) -> None:
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama", "maxtokens.json"))
    )
    res = adapter().generate([ChatMessage.user("story")], max_tokens=8)
    assert res.finish_reason == "length"
    assert json.loads(respx.calls[0].request.content)["options"] == {"num_predict": 8}


# ---------------------------------------------------------------- 送信前の検査


def test_builtin_tools_rejected() -> None:
    with pytest.raises(UnsupportedFeatureError, match="ToolRunner"):
        adapter().generate([ChatMessage.user("x")], tools=[BuiltinTool.WEB_SEARCH])


@respx.mock
def test_capability_check_blocks_tools_on_model_without_tools() -> None:
    show = mock_show({"capabilities": ["completion", "vision", "audio"]})  # 実測: gemma4:e4b-mlx
    chat = respx.post(f"{BASE}/api/chat")
    a = adapter()
    for _ in range(2):
        with pytest.raises(UnsupportedFeatureError, match="tools"):
            a.generate([ChatMessage.user("x")], tools=[get_weather])
    assert show.call_count == 1  # キャッシュされる
    assert chat.call_count == 0


def test_tool_choice_required_unsupported() -> None:
    with pytest.raises(UnsupportedFeatureError):
        adapter(check_capabilities=False).generate(
            [ChatMessage.user("x")], tools=[get_weather], tool_choice="required"
        )


@respx.mock
def test_images_and_options(tmp_path: Path, fixture_bytes) -> None:
    from omni_ai import MediaPart, TextPart, UrlRef

    mock_show()
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama", "text.json"))
    )
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    msg = ChatMessage.user(
        [TextPart(text="what?"), MediaPart(type="image", source=img, mime_type="image/png")]
    )
    adapter().generate([msg], temperature=0.2, ollama={"num_ctx": 8192, "keep_alive": "1m"}, gemini={"x": 1})
    body = json.loads(route.calls[0].request.content)
    assert body["messages"][0] == {"role": "user", "content": "what?", "images": ["iVBORw=="]}
    assert body["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert body["keep_alive"] == "1m"
    with pytest.raises(UnsupportedFeatureError):
        adapter().generate(
            [
                ChatMessage.user(
                    [MediaPart(type="image", source=UrlRef(url="https://x/a.png"), mime_type="image/png")]
                )
            ]
        )
    with pytest.raises(UnsupportedFeatureError):
        adapter().generate(
            [ChatMessage.user([MediaPart(type="document", source=b"%PDF", mime_type="application/pdf")])]
        )


@respx.mock
def test_stream_error_line_after_output_is_interrupted() -> None:
    lines = (
        json.dumps({"model": MODEL, "message": {"role": "assistant", "content": "Hel"}, "done": False})
        + "\n"
        + json.dumps({"error": "an error was encountered while running the model"})
        + "\n"
    ).encode()
    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(200, content=lines))
    it = adapter().stream([ChatMessage.user("x")])
    assert next(it).delta_text == "Hel"
    with pytest.raises(StreamInterruptedError):
        next(it)


@respx.mock
def test_stream_error_line_before_output_is_plain_error() -> None:
    respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(200, content=b'{"error": "boom"}\n'))
    with pytest.raises(ProviderOverloadedError):
        list(adapter().stream([ChatMessage.user("x")]))


@respx.mock
def test_stream_without_done_is_error() -> None:
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200,
            content=json.dumps({"model": MODEL, "message": {"content": "a"}, "done": False}).encode() + b"\n",
        )
    )
    with pytest.raises(StreamInterruptedError):
        list(adapter().stream([ChatMessage.user("x")]))


def test_model_required() -> None:
    a = OllamaChatAdapter(base_url=BASE)
    with pytest.raises(OmniError, match="model is required"):
        a.generate([ChatMessage.user("x")])


def test_base_url_from_ollama_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNI_OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert OllamaChatAdapter(default_model="m").base_url == "http://127.0.0.1:11434"


@respx.mock
def test_cloud_model_keeps_requested_name_for_pricing() -> None:
    # 実測 (Ollama 0.40.6): ":cloud" / "-cloud" のモデルでも、応答の model は "gemma4:31b"（接尾辞なし）
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gemma4:31b",
                "message": {"role": "assistant", "content": "OK"},
                "done": True,
                "done_reason": "stop",
            },
        )
    )
    from omni_ai import OmniClient

    ai = OmniClient(chat=adapter())
    res = ai.chat.generate([ChatMessage.user("hi")])
    assert res.meta.model == "gemma4:31b-cloud"
    assert res.meta.estimated_cost_usd is None and ai.telemetry.unpriced_calls == 1
