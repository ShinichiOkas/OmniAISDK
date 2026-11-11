"""Anthropic（Claude）Messages API の契約テスト。

応答は Anthropic の公開仕様（Messages API・ストリーミング・エラー）に沿って手で作った**合成データ**
（実 API の記録ではない）。実物での確認は未実施（鍵が無い。師匠の検収待ち）。
"""

import json
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from omni_ai import (
    AuthenticationError,
    ChatMessage,
    ContextLengthExceededError,
    InvalidRequestError,
    NotFoundError,
    OmniClient,
    PermissionDeniedError,
    ProviderOverloadedError,
    QuotaExceededError,
    RateLimitError,
    StreamInterruptedError,
    UnsupportedFeatureError,
    omni_tool,
)
from omni_ai._http import RetryPolicy
from omni_ai.pricing import ModelPrice, PricingTable
from omni_ai.providers.anthropic import AnthropicChatAdapter
from omni_ai.providers.openai_compatible import OpenAICompatibleChatAdapter
from omni_ai.types import MediaPart, TextPart, ToolCall, UrlRef

BASE = "https://api.anthropic.com"
URL = f"{BASE}/v1/messages"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    for var in ("ANTHROPIC_API_KEY", "OMNI_ANTHROPIC_API_KEY", "OMNI_ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city"""
    return "sunny"


class Answer(BaseModel):
    city: str
    temp: int


def make(**kw) -> AnthropicChatAdapter:
    params = {
        "api_key": "test-key",
        "default_model": "claude-sonnet-5-5",
        "retry": RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0),
    }
    params.update(kw)
    a = AnthropicChatAdapter(**params)
    a._sync._sleep = lambda _s: None
    return a


def message(content=None, *, stop="end_turn", usage=None) -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5-20261101",
        "content": content if content is not None else [{"type": "text", "text": "OK"}],
        "stop_reason": stop,
        "usage": usage or {"input_tokens": 12, "output_tokens": 7},
    }


def sse(*events: dict) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def start(usage=None) -> dict:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "content": [],
            "usage": usage or {"input_tokens": 25, "output_tokens": 1},
        },
    }


def block_start(index: int, block: dict) -> dict:
    return {"type": "content_block_start", "index": index, "content_block": block}


def delta(index: int, d: dict) -> dict:
    return {"type": "content_block_delta", "index": index, "delta": d}


def block_stop(index: int) -> dict:
    return {"type": "content_block_stop", "index": index}


def msg_delta(stop="end_turn", output_tokens=15) -> dict:
    return {
        "type": "message_delta",
        "delta": {"stop_reason": stop},
        "usage": {"output_tokens": output_tokens},
    }


STOP = {"type": "message_stop"}


# ---------------------------------------------------------------------------- 構築・送信


def test_requires_key() -> None:
    with pytest.raises(AuthenticationError):
        AnthropicChatAdapter(default_model="m")


def test_key_from_standard_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    with respx.mock:
        route = respx.post(URL).mock(return_value=httpx.Response(200, json=message()))
        AnthropicChatAdapter(default_model="m").generate([ChatMessage.user("hi")])
    assert route.calls[0].request.headers["x-api-key"] == "env-key"


@respx.mock
def test_text_usage_and_wire() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=message(
                usage={
                    "input_tokens": 10,
                    "cache_read_input_tokens": 30,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": 7,
                }
            ),
        )
    )
    res = make().generate(
        [ChatMessage.system("be brief"), ChatMessage.user("hi")], temperature=0.2, max_tokens=50
    )
    assert res.content == "OK" and res.finish_reason == "stop"
    # 応答の model（日付付き）ではなく、指定した名前を記録する
    assert res.meta.provider == "anthropic" and res.meta.model == "claude-sonnet-5-5"
    assert res.meta.request_id == "msg_1"
    u = res.meta.usage
    # input_tokens はキャッシュ分を含まない。prompt には足し戻し、cached は読んだ分だけ
    assert (u.prompt_tokens, u.completion_tokens, u.cached_tokens, u.total_tokens) == (45, 7, 30, 52)
    assert u.reasoning_tokens == 0
    req = route.calls[0].request
    assert req.headers["x-api-key"] == "test-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in req.headers
    assert json.loads(req.content) == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 50,
        "temperature": 0.2,
        "system": "be brief",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }


@respx.mock
def test_defaults_send_only_what_the_caller_set() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=message()))
    make().generate([ChatMessage.user("hi")])
    body = json.loads(route.calls[0].request.content)
    # 新しいモデルは既定外のサンプリング指定を 400 で断る。何も指定しなければ何も送らない
    assert set(body) == {"model", "max_tokens", "messages"}
    assert body["max_tokens"] == 16000
    make(default_max_tokens=2000).generate([ChatMessage.user("hi")])
    assert json.loads(route.calls[-1].request.content)["max_tokens"] == 2000


@respx.mock
def test_custom_base_url_and_passthrough_options() -> None:
    route = respx.post("https://proxy.example.com/v1/messages").mock(
        return_value=httpx.Response(200, json=message())
    )
    make(base_url="https://proxy.example.com/").generate(
        [ChatMessage.user("hi")],
        anthropic={
            "top_p": 0.9,
            "stop_sequences": ["END"],
            "thinking": {"type": "adaptive"},
            "effort": "high",
            "extra_body": {"service_tier": "auto"},
        },
    )
    body = json.loads(route.calls[0].request.content)
    assert body["top_p"] == 0.9 and body["stop_sequences"] == ["END"]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"}
    assert body["service_tier"] == "auto"


# ---------------------------------------------------------------------------- ツール


@respx.mock
def test_tool_use_roundtrip() -> None:
    content = [
        {"type": "text", "text": "checking"},
        {"type": "tool_use", "id": "toolu_a", "name": "get_weather", "input": {"city": "Tokyo"}},
        {"type": "tool_use", "id": "toolu_b", "name": "get_weather", "input": {}},
    ]
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(200, json=message(content, stop="tool_use")),
            httpx.Response(200, json=message([{"type": "text", "text": "sunny in Tokyo"}])),
        ]
    )
    a = make()
    msgs = [ChatMessage.user("weather?")]
    res = a.generate(msgs, tools=[get_weather], tool_choice="get_weather")
    assert res.finish_reason == "tool_calls"
    assert [(t.id, t.name, t.arguments) for t in res.tool_calls] == [
        ("toolu_a", "get_weather", {"city": "Tokyo"}),
        ("toolu_b", "get_weather", {}),
    ]
    first = json.loads(route.calls[0].request.content)
    tool = first["tools"][0]
    assert tool["name"] == "get_weather" and tool["description"] == "Get current weather for a city"
    assert tool["input_schema"]["properties"]["city"]["type"] == "string"
    assert first["tool_choice"] == {"type": "tool", "name": "get_weather"}

    msgs.append(res.to_message())
    msgs.append(ChatMessage.tool("sunny", tool_call_id="toolu_a", name="get_weather"))
    msgs.append(ChatMessage.tool("unknown", tool_call_id="toolu_b", name="get_weather"))
    assert a.generate(msgs, tools=[get_weather]).content == "sunny in Tokyo"
    second = json.loads(route.calls[1].request.content)
    assert second["tool_choice"] == {"type": "auto"}
    # ツール結果は 1 つの user にまとめ、assistant の content は応答のまま返す
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert second["messages"][1]["content"] == content
    assert second["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_a", "content": "sunny"},
        {"type": "tool_result", "tool_use_id": "toolu_b", "content": "unknown"},
    ]


@respx.mock
def test_required_tool_choice_maps_to_any() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=message()))
    make().generate([ChatMessage.user("x")], tools=[get_weather], tool_choice="required")
    assert json.loads(route.calls[0].request.content)["tool_choice"] == {"type": "any"}


@respx.mock
def test_foreign_history_is_rebuilt_with_safe_ids() -> None:
    """フォールバックで他社の履歴が来ても、provider_state が無ければ共通の形から組み直す。"""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=message()))
    foreign = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call:abc/1", name="get_weather", arguments={"city": "Osaka"})],
        provider_state={"gemini": {"parts": [{"thought": "x"}]}},  # 他社の状態は読まない
    )
    msgs = [
        ChatMessage.user("w?"),
        foreign,
        ChatMessage.tool("rain", tool_call_id="call:abc/1", name="get_weather"),
        ChatMessage.user("and then?"),
    ]
    make().generate(msgs, tools=[get_weather])
    sent = json.loads(route.calls[0].request.content)["messages"]
    assert sent[1]["content"] == [
        {"type": "tool_use", "id": "call_abc_1", "name": "get_weather", "input": {"city": "Osaka"}}
    ]
    # tool_result の id は tool_use と同じ変換を受け、続く user の本文は同じ user にまとめる
    assert sent[2]["role"] == "user"
    assert sent[2]["content"][0] == {"type": "tool_result", "tool_use_id": "call_abc_1", "content": "rain"}
    assert sent[2]["content"][1] == {"type": "text", "text": "and then?"}
    assert len(sent) == 3


@respx.mock
def test_consecutive_same_role_messages_are_merged() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=message()))
    make().generate([ChatMessage.user("a"), ChatMessage.user("b")])
    sent = json.loads(route.calls[0].request.content)["messages"]
    assert sent == [
        {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    ]


# ---------------------------------------------------------------------------- 思考


@respx.mock
def test_thinking_blocks_are_replayed_unchanged() -> None:
    content = [
        {"type": "thinking", "thinking": "hmm", "signature": "sig-123"},
        {"type": "redacted_thinking", "data": "opaque"},
        {"type": "text", "text": "answer"},
    ]
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(200, json=message(content)), httpx.Response(200, json=message())]
    )
    a = make()
    msgs = [ChatMessage.user("q")]
    res = a.generate(msgs, anthropic={"thinking": {"type": "adaptive"}})
    assert res.content == "answer" and res.reasoning == "hmm"
    msgs += [res.to_message(), ChatMessage.user("next")]
    a.generate(msgs, anthropic={"thinking": {"type": "adaptive"}})
    sent = json.loads(route.calls[1].request.content)["messages"]
    assert sent[1] == {"role": "assistant", "content": content}


# ---------------------------------------------------------------------------- 構造化出力・メディア


@respx.mock
def test_structured_output() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200, json=message([{"type": "text", "text": '```json\n{"city": "Tokyo", "temp": 20}\n```'}])
        )
    )
    res = make().generate([ChatMessage.user("weather")], response_schema=Answer)
    assert res.parsed == Answer(city="Tokyo", temp=20)
    body = json.loads(route.calls[0].request.content)
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["required"] == ["city", "temp"] and fmt["schema"]["additionalProperties"] is False
    # どの方式でもスキーマを指示文で添える
    assert '"city"' in body["system"]


@respx.mock
def test_structured_output_prompt_mode_and_user_system_kept() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json=message([{"type": "text", "text": '{"city": "a", "temp": 1}'}]))
    )
    res = make().generate(
        [ChatMessage.system("be terse"), ChatMessage.user("w")],
        response_schema=Answer,
        anthropic={"response_format": "prompt"},
    )
    assert res.parsed == Answer(city="a", temp=1)
    body = json.loads(route.calls[0].request.content)
    assert "output_config" not in body
    assert body["system"].startswith("be terse") and '"temp"' in body["system"]


@respx.mock
def test_images_and_pdf() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=message()))
    content = [
        TextPart(text="what is this"),
        MediaPart(type="image", source=b"\x89PNG", mime_type="image/png"),
        MediaPart(type="image", source=UrlRef(url="https://example.com/a.png"), mime_type="image/png"),
        MediaPart(type="document", source=b"%PDF-1.4", mime_type="application/pdf"),
    ]
    make().generate([ChatMessage.user(content)])
    parts = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this"}
    assert parts[1]["type"] == "image" and parts[1]["source"]["type"] == "base64"
    assert parts[1]["source"]["media_type"] == "image/png"
    assert parts[2] == {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}
    assert parts[3]["type"] == "document" and parts[3]["source"]["media_type"] == "application/pdf"


@respx.mock
def test_audio_is_unsupported() -> None:
    with pytest.raises(UnsupportedFeatureError):
        make().generate([ChatMessage.user([MediaPart(type="audio", source=b"RIFF", mime_type="audio/wav")])])


# ---------------------------------------------------------------------------- ストリーム


def text_stream() -> bytes:
    return sse(
        start(),
        block_start(0, {"type": "text", "text": ""}),
        {"type": "ping"},
        delta(0, {"type": "text_delta", "text": "Hel"}),
        delta(0, {"type": "text_delta", "text": "lo"}),
        block_stop(0),
        msg_delta(),
        STOP,
    )


@respx.mock
def test_stream_text_and_usage_merge() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, content=text_stream()))
    events = list(make().stream([ChatMessage.user("hi")]))
    assert json.loads(route.calls[0].request.content)["stream"] is True
    assert "".join(e.delta_text or "" for e in events if e.type == "text_delta") == "Hello"
    done = events[-1]
    assert done.type == "message_complete" and done.finish_reason == "stop"
    assert done.response.content == "Hello"
    # 入力側は message_start、出力側は message_delta（累積）から合わせる
    u = done.meta.usage
    assert (u.prompt_tokens, u.completion_tokens, u.total_tokens) == (25, 15, 40)
    assert done.meta.request_id == "msg_1" and done.meta.model == "claude-sonnet-5-5"


@respx.mock
def test_stream_survives_arbitrary_chunking() -> None:
    data = text_stream()
    respx.post(URL).mock(
        return_value=httpx.Response(200, stream=httpx.ByteStream(data))  # 1 バイトずつ来る回線を模す
    )
    a = make()
    state = a._new_stream_state(type("R", (), {"model": "m", "response_schema": None, "options": {}})())
    events = []
    for i in range(len(data)):
        events += state.feed(data[i : i + 1])
    events += state.finish()
    assert "".join(e.delta_text or "" for e in events if e.type == "text_delta") == "Hello"
    assert events[-1].type == "message_complete"


@respx.mock
def test_stream_tool_call_and_thinking() -> None:
    body = sse(
        start(),
        block_start(0, {"type": "thinking", "thinking": ""}),
        delta(0, {"type": "thinking_delta", "thinking": "let me "}),
        delta(0, {"type": "thinking_delta", "thinking": "see"}),
        delta(0, {"type": "signature_delta", "signature": "sig-9"}),
        block_stop(0),
        block_start(1, {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}}),
        delta(1, {"type": "input_json_delta", "partial_json": ""}),
        delta(1, {"type": "input_json_delta", "partial_json": '{"city": '}),
        delta(1, {"type": "input_json_delta", "partial_json": '"Tokyo"}'}),
        block_stop(1),
        block_start(2, {"type": "tool_use", "id": "toolu_2", "name": "get_weather", "input": {}}),
        block_stop(2),  # 入力なし
        block_start(3, {"type": "future_block", "x": 1}),  # 知らないブロックは読み飛ばす
        {"type": "future_event"},
        block_stop(3),
        msg_delta("tool_use"),
        STOP,
    )
    respx.post(URL).mock(return_value=httpx.Response(200, content=body))
    events = list(make().stream([ChatMessage.user("w")], tools=[get_weather]))
    assert "".join(e.delta_text or "" for e in events if e.type == "reasoning_delta") == "let me see"
    calls = [e.tool_call for e in events if e.type == "tool_call"]
    assert [(c.id, c.arguments) for c in calls] == [("toolu_1", {"city": "Tokyo"}), ("toolu_2", {})]
    done = events[-1]
    assert done.finish_reason == "tool_calls" and done.response.reasoning == "let me see"
    # 次の要求で返せるよう、署名つきの thinking を含む content がそのまま残る
    stored = done.response.provider_state["anthropic"]["content"]
    assert stored[0] == {"type": "thinking", "thinking": "let me see", "signature": "sig-9"}
    assert stored[1]["input"] == {"city": "Tokyo"}


@respx.mock
def test_stream_error_event_on_http_200() -> None:
    body = sse(
        start(),
        block_start(0, {"type": "text", "text": ""}),
        delta(0, {"type": "text_delta", "text": "part"}),
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    respx.post(URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises((ProviderOverloadedError, StreamInterruptedError)) as exc:
        list(make().stream([ChatMessage.user("x")]))
    assert "Overloaded" in str(exc.value) or "Overloaded" in str(exc.value.__cause__)


@respx.mock
def test_stream_without_stop_is_error() -> None:
    body = sse(
        start(), block_start(0, {"type": "text", "text": ""}), delta(0, {"type": "text_delta", "text": "x"})
    )
    respx.post(URL).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(StreamInterruptedError):
        list(make().stream([ChatMessage.user("x")]))


@respx.mock
@pytest.mark.asyncio
async def test_async_paths() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(200, json=message()),
            httpx.Response(200, content=text_stream()),
        ]
    )
    a = make()
    res = await a.agenerate([ChatMessage.user("x")])
    assert res.content == "OK"
    events = [e async for e in a.astream([ChatMessage.user("x")])]
    assert events[-1].type == "message_complete" and events[-1].response.content == "Hello"
    assert route.call_count == 2


# ---------------------------------------------------------------------------- エラー


def err(kind: str, message: str) -> dict:
    return {"type": "error", "error": {"type": kind, "message": message}}


ERRORS = [
    (400, err("invalid_request_error", "messages: field required"), InvalidRequestError),
    (
        400,
        err("invalid_request_error", "prompt is too long: 250000 tokens > 200000 maximum"),
        ContextLengthExceededError,
    ),
    (
        400,
        err("invalid_request_error", "`temperature` is not supported for this model"),
        UnsupportedFeatureError,
    ),
    (401, err("authentication_error", "invalid x-api-key"), AuthenticationError),
    (402, err("billing_error", "credit balance too low"), QuotaExceededError),
    (403, err("permission_error", "not allowed"), PermissionDeniedError),
    (404, err("not_found_error", "model: nope"), NotFoundError),
    (
        413,
        err("request_too_large", "Request exceeds the maximum allowed number of bytes"),
        InvalidRequestError,
    ),
]


@pytest.mark.parametrize(("status", "payload", "exc"), ERRORS)
@respx.mock
def test_error_mapping(status, payload, exc) -> None:
    respx.post(URL).mock(return_value=httpx.Response(status, json=payload))
    with pytest.raises(exc) as info:
        make().generate([ChatMessage.user("x")])
    assert info.value.provider == "anthropic"
    assert info.value.status_code == status


@respx.mock
def test_rate_limit_retry_after_and_retry() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, json=err("rate_limit_error", "slow down"), headers={"retry-after": "3"}),
            httpx.Response(200, json=message()),
        ]
    )
    assert make().generate([ChatMessage.user("x")]).content == "OK"
    assert route.call_count == 2
    respx.post(URL).mock(
        return_value=httpx.Response(
            429, json=err("rate_limit_error", "slow down"), headers={"retry-after": "3"}
        )
    )
    with pytest.raises(RateLimitError) as info:
        make(retry=RetryPolicy(max_attempts=1, base_delay=0.0, jitter=0.0)).generate([ChatMessage.user("x")])
    assert info.value.retry_after_seconds == 3.0


@respx.mock
def test_overloaded_529_is_retried() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(529, json=err("overloaded_error", "Overloaded")),
            httpx.Response(200, json=message()),
        ]
    )
    assert make().generate([ChatMessage.user("x")]).content == "OK"
    assert route.call_count == 2
    respx.post(URL).mock(return_value=httpx.Response(529, json=err("overloaded_error", "Overloaded")))
    with pytest.raises(ProviderOverloadedError):
        make().generate([ChatMessage.user("x")])


@pytest.mark.parametrize(
    ("stop", "expected"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("refusal", "content_filter"),
        ("something_new", "unknown"),
    ],
)
@respx.mock
def test_stop_reason_mapping(stop, expected) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=message(stop=stop)))
    assert make().generate([ChatMessage.user("x")]).finish_reason == expected


# ---------------------------------------------------------------------------- 配線・費用


@respx.mock
def test_cost_uses_prompt_including_cache() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=message(usage={"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 50}),
        )
    )
    res = make().generate([ChatMessage.user("x")])
    table = PricingTable(
        {
            ("anthropic", "claude-sonnet-5-5"): ModelPrice(
                input_per_mtok=2.0, output_per_mtok=10.0, cached_input_per_mtok=0.2
            )
        }
    )
    cost = table.estimate(res.meta)
    # 非キャッシュ 100 トークン × $2 + キャッシュ 900 × $0.2 + 出力 50 × $10（/ 1M）
    assert cost == pytest.approx((100 * 2.0 + 900 * 0.2 + 50 * 10.0) / 1_000_000)


@respx.mock
@pytest.mark.asyncio
async def test_fallback_to_anthropic_after_local_failure() -> None:
    respx.post("http://127.0.0.1:8080/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": {"message": "down"}})
    )
    respx.post(URL).mock(return_value=httpx.Response(200, json=message()))
    from omni_ai.router import FallbackChatAdapter

    local = OpenAICompatibleChatAdapter(
        base_url="http://127.0.0.1:8080",
        api_key="k",
        default_model="m",
        retry=RetryPolicy(max_attempts=1, base_delay=0.0, jitter=0.0),
    )
    ai = OmniClient(chat=FallbackChatAdapter([local, make()]))
    res = await ai.chat.agenerate([ChatMessage.user("x")])
    assert res.content == "OK" and res.meta.provider == "anthropic"
    assert res.meta.fallback_from == ["openai-compatible"]
    await ai.aclose()
