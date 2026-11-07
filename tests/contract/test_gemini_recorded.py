"""GeminiChatAdapter の契約テスト — 実 API から記録した応答（fixtures/gemini, gemini-3.1-flash-lite）。"""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from omni_ai import (
    AuthenticationError,
    ChatMessage,
    InvalidRequestError,
    NotFoundError,
    RateLimitError,
    omni_tool,
)
from omni_ai._http import RetryPolicy
from omni_ai.providers.gemini import DEFAULT_MODEL, GeminiChatAdapter

BASE = "https://gemini.test/v1beta"
MODEL = "gemini-3.1-flash-lite"
GEN = f"{BASE}/models/{MODEL}:generateContent"
STREAM = f"{BASE}/models/{MODEL}:streamGenerateContent"


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city"""
    return "sunny, 20C"


def adapter() -> GeminiChatAdapter:
    a = GeminiChatAdapter(api_key="k", base_url=BASE, retry=RetryPolicy(max_attempts=2, base_delay=0.0))
    a._sync._sleep = lambda _s: None
    return a


def recorded_request(fixture_bytes, name: str) -> dict:
    return json.loads(fixture_bytes("gemini", f"{name}.request.json"))["body"]


def test_default_model_is_the_measured_one() -> None:
    assert DEFAULT_MODEL == MODEL


@respx.mock
def test_text(fixture_bytes) -> None:
    respx.post(GEN).mock(return_value=httpx.Response(200, content=fixture_bytes("gemini", "text.json")))
    res = adapter().generate([ChatMessage.user("Reply with exactly: OK")])
    assert res.content == "OK" and res.finish_reason == "stop"
    assert res.meta.model == MODEL and res.meta.request_id
    assert (res.meta.usage.prompt_tokens, res.meta.usage.completion_tokens, res.meta.usage.total_tokens) == (
        6,
        1,
        7,
    )
    # 関数呼び出しでない text にも署名が付く → parts を丸ごと保持している
    assert "thoughtSignature" in res.provider_state["gemini"]["content"]["parts"][0]


@respx.mock
def test_parallel_tools_roundtrip_matches_recorded_wire(fixture_bytes) -> None:
    route = respx.post(GEN).mock(
        side_effect=[
            httpx.Response(200, content=fixture_bytes("gemini", "tools.json")),
            httpx.Response(200, content=fixture_bytes("gemini", "tools_followup.json")),
        ]
    )
    a = adapter()
    msgs = [ChatMessage.user("What's the weather in Tokyo and in Osaka? Call the tool once for each city.")]
    res = a.generate(msgs, tools=[get_weather])
    assert res.finish_reason == "tool_calls"
    assert [(c.name, c.arguments) for c in res.tool_calls] == [
        ("get_weather", {"city": "Tokyo"}),
        ("get_weather", {"city": "Osaka"}),
    ]
    assert all(c.id.startswith("call_") for c in res.tool_calls)

    history = [*msgs, res.to_message()]
    history += [
        ChatMessage.tool(get_weather(**c.arguments), tool_call_id=c.id, name=c.name) for c in res.tool_calls
    ]
    final = a.generate(history, tools=[get_weather])
    assert final.content and "20" in final.content

    # SDK が送った履歴が、実 API で 200 になった要求と同じ形であること
    sent = json.loads(route.calls[1].request.content)["contents"]
    expected = recorded_request(fixture_bytes, "tools_followup")["contents"]
    assert sent == expected


def test_signature_removal_is_rejected_by_api(fixture_bytes) -> None:
    """実測の裏付け: 署名を外すと 400。SDK は署名を外さない（上のテストで送信内容を確認）。"""
    body = json.loads(fixture_bytes("gemini", "tools_followup_nosig.json"))
    assert body["error"]["code"] == 400 and "thought_signature" in body["error"]["message"]


@respx.mock
def test_stream_text(fixture_bytes) -> None:
    respx.post(STREAM).mock(
        return_value=httpx.Response(200, content=fixture_bytes("gemini", "text_stream.sse"))
    )
    events = list(adapter().stream([ChatMessage.user("Count")]))
    text = "".join(e.delta_text for e in events if e.type == "text_delta")
    assert text == "1 2 3 4 5"
    final = events[-1]
    assert final.type == "message_complete" and final.response is not None
    assert final.response.content == text and final.response.finish_reason == "stop"
    assert final.response.meta.usage.total_tokens == 22
    # 最後のチャンクの空 text に付いた署名も保持する
    assert any("thoughtSignature" in p for p in final.response.provider_state["gemini"]["content"]["parts"])


@respx.mock
async def test_stream_tools(fixture_bytes) -> None:
    respx.post(STREAM).mock(
        return_value=httpx.Response(200, content=fixture_bytes("gemini", "tools_stream.sse"))
    )
    a = adapter()
    events = [e async for e in a.astream([ChatMessage.user("weather")], tools=[get_weather])]
    assert [e.tool_call.arguments["city"] for e in events if e.type == "tool_call" and e.tool_call] == [
        "Tokyo",
        "Osaka",
    ]
    assert events[-1].finish_reason == "tool_calls"
    await a.aclose()


class Person(BaseModel):
    name: str
    age: int


@respx.mock
def test_schema_and_max_tokens(fixture_bytes) -> None:
    respx.post(GEN).mock(
        side_effect=[
            httpx.Response(200, content=fixture_bytes("gemini", "schema.json")),
            httpx.Response(200, content=fixture_bytes("gemini", "maxtokens.json")),
        ]
    )
    a = adapter()
    assert a.generate([ChatMessage.user("Ann 30")], response_schema=Person).parsed == Person(
        name="Ann", age=30
    )
    assert a.generate([ChatMessage.user("story")], max_tokens=8).finish_reason == "length"


@respx.mock
def test_prefill_is_sent_not_rejected(fixture_bytes) -> None:
    route = respx.post(GEN).mock(
        return_value=httpx.Response(200, content=fixture_bytes("gemini", "prefill.json"))
    )
    res = adapter().generate([ChatMessage.user("Say hi"), ChatMessage.assistant("Hel")])
    assert res.content and res.content.startswith("o!")
    assert json.loads(route.calls[0].request.content)["contents"][-1]["role"] == "model"


@pytest.mark.parametrize(
    ("name", "status", "exc", "calls"),
    [
        ("err_badkey", 400, AuthenticationError, 1),
        ("err_badmodel", 404, NotFoundError, 1),
        ("err_badrole", 400, InvalidRequestError, 1),
        ("tools_followup_nosig", 400, InvalidRequestError, 1),
        ("err_search_quota", 429, RateLimitError, 2),
    ],
)
@respx.mock
def test_recorded_errors(fixture_bytes, name: str, status: int, exc: type, calls: int) -> None:
    route = respx.post(GEN).mock(
        return_value=httpx.Response(status, content=fixture_bytes("gemini", f"{name}.json"))
    )
    with pytest.raises(exc):
        adapter().generate([ChatMessage.user("hi")])
    assert route.call_count == calls
