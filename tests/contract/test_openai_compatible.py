"""OpenAI 互換 Chat（xAI・Agnes・OpenRouter・openai-compatible）と Ollama Cloud の契約テスト。

応答は OpenAI の /chat/completions の公開仕様に沿って手で作った**合成データ**（実 API の記録ではない）。
実物での確認は未実施（師匠の決定: 偽物のテストだけで進める）。
"""

import json
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from omni_ai import (
    AuthenticationError,
    BuiltinTool,
    ChatMessage,
    ContextLengthExceededError,
    InvalidRequestError,
    NotFoundError,
    OmniClient,
    OmniError,
    ProviderOverloadedError,
    QuotaExceededError,
    RateLimitError,
    StreamInterruptedError,
    UnsupportedFeatureError,
    omni_tool,
)
from omni_ai._http import RetryPolicy
from omni_ai.pricing import ModelPrice, PricingTable
from omni_ai.providers.ollama import OllamaCloudChatAdapter
from omni_ai.providers.openai_compatible import (
    AgnesChatAdapter,
    OpenAICompatibleChatAdapter,
    OpenRouterChatAdapter,
    XAIChatAdapter,
)
from omni_ai.types import MediaPart, TextPart, UrlRef

LOCAL = "http://127.0.0.1:8080"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    for var in (
        "XAI_API_KEY",
        "AGNES_API_KEY",
        "OPENROUTER_API_KEY",
        "OLLAMA_API_KEY",
        "OMNI_OPENAI_COMPATIBLE_BASE_URL",
        "OMNI_OPENAI_COMPATIBLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city"""
    return "sunny"


# (アダプター, 既定の URL ＋ /chat/completions, 構造化出力の既定)
CASES = [
    (XAIChatAdapter, "https://api.x.ai/v1", "json_schema"),
    (AgnesChatAdapter, "https://apihub.agnes-ai.com/v1", "json_object"),
    (OpenRouterChatAdapter, "https://openrouter.ai/api/v1", "json_schema"),
    (OpenAICompatibleChatAdapter, f"{LOCAL}/v1", "json_schema"),
]
IDS = ["xai", "agnes", "openrouter", "openai-compatible"]


def make(cls, **kw):
    params = {
        "api_key": "test-key",
        "default_model": "m-1",
        "retry": RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0),
    }
    if cls is OpenAICompatibleChatAdapter:
        params["base_url"] = LOCAL
    params.update(kw)
    a = cls(**params)
    a._sync._sleep = lambda _s: None
    return a


def completion(content="OK", *, tool_calls=None, finish="stop", usage=None, reasoning=None) -> dict:
    msg: dict = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "model": "routed/m-1",
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": usage
        or {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
            "prompt_tokens_details": {"cached_tokens": 4},
            "completion_tokens_details": {"reasoning_tokens": 3},
        },
    }


def sse(*objs, done: bool = True) -> bytes:
    lines = [f"data: {json.dumps(o)}\n\n" for o in objs]
    if done:
        lines.append("data: [DONE]\n\n")
    return ": keep-alive\n\n".join(lines).encode()


def chunk(delta: dict, finish=None, usage=None) -> dict:
    obj: dict = {"id": "gen-1", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        obj = {"id": "gen-1", "choices": [], "usage": usage}
    return obj


# ---------------------------------------------------------------------------- 共通の振る舞い


@pytest.mark.parametrize(("cls", "base", "fmt"), CASES, ids=IDS)
@respx.mock
def test_text_usage_and_wire(cls, base, fmt) -> None:
    route = respx.post(f"{base}/chat/completions").mock(return_value=httpx.Response(200, json=completion()))
    res = make(cls).generate(
        [ChatMessage.system("be brief"), ChatMessage.user("hi")], temperature=0.2, max_tokens=50
    )
    assert res.content == "OK" and res.finish_reason == "stop"
    # 応答の model（ルーティング先）ではなく、指定した名前を記録する
    assert res.meta.provider == cls.provider_name and res.meta.model == "m-1"
    u = res.meta.usage
    assert (u.prompt_tokens, u.completion_tokens, u.reasoning_tokens, u.cached_tokens, u.total_tokens) == (
        12,
        4,
        3,
        4,
        19,
    )
    req = route.calls[0].request
    assert req.headers["authorization"] == "Bearer test-key"
    # 帰属ヘッダーは付けない（OpenRouter の公開ランキングに載らないように）
    assert "http-referer" not in req.headers and "x-title" not in req.headers
    body = json.loads(req.content)
    assert body == {
        "model": "m-1",
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "max_tokens": 50,
    }


@pytest.mark.parametrize(("cls", "base", "fmt"), CASES, ids=IDS)
@respx.mock
def test_tool_calls_roundtrip(cls, base, fmt) -> None:
    calls = [
        {
            "id": "call_a",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Tokyo"}'},
        },
        {"id": "call_b", "type": "function", "function": {"name": "get_weather", "arguments": "not json"}},
    ]
    route = respx.post(f"{base}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=completion(None, tool_calls=calls, finish="tool_calls")),
            httpx.Response(200, json=completion("sunny in Tokyo")),
        ]
    )
    a = make(cls)
    msgs = [ChatMessage.user("weather?")]
    res = a.generate(msgs, tools=[get_weather], tool_choice="get_weather")
    assert res.finish_reason == "tool_calls"
    assert [(t.id, t.name, t.arguments) for t in res.tool_calls] == [
        ("call_a", "get_weather", {"city": "Tokyo"}),
        ("call_b", "get_weather", {"_raw": "not json"}),
    ]
    first = json.loads(route.calls[0].request.content)
    assert first["tools"][0]["function"]["name"] == "get_weather"
    assert first["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}
    msgs.append(ChatMessage(role="assistant", content="", tool_calls=res.tool_calls))
    msgs.append(ChatMessage.tool("sunny", tool_call_id="call_a", name="get_weather"))
    assert a.generate(msgs, tools=[get_weather]).content == "sunny in Tokyo"
    second = json.loads(route.calls[1].request.content)
    assert second["tool_choice"] == "auto"
    assistant, tool = second["messages"][1], second["messages"][2]
    assert assistant["content"] is None
    assert assistant["tool_calls"][0] == {
        "id": "call_a",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Tokyo"}'},
    }
    assert tool == {"role": "tool", "content": "sunny", "tool_call_id": "call_a", "name": "get_weather"}


@pytest.mark.parametrize(("cls", "base", "fmt"), CASES, ids=IDS)
@respx.mock
def test_stream_assembles_tool_call_fragments(cls, base, fmt) -> None:
    body = sse(
        chunk({"role": "assistant", "reasoning_content": "thinking"}),
        chunk({"content": "Hel"}),
        chunk({"content": "lo"}),
        chunk(
            {
                "tool_calls": [
                    {"index": 0, "id": "call_x", "function": {"name": "get_weather", "arguments": '{"ci'}}
                ]
            }
        ),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'ty": "Osaka"}'}}]}),
        chunk({}, finish="tool_calls"),
        chunk({}, usage={"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14}),
    )
    route = respx.post(f"{base}/chat/completions").mock(return_value=httpx.Response(200, content=body))
    events = list(make(cls).stream([ChatMessage.user("hi")], tools=[get_weather]))
    kinds = [e.type for e in events]
    assert kinds == ["reasoning_delta", "text_delta", "text_delta", "tool_call", "message_complete"]
    final = events[-1].response
    assert final.content == "Hello" and final.reasoning == "thinking"
    assert final.tool_calls[0].arguments == {"city": "Osaka"} and final.finish_reason == "tool_calls"
    assert final.meta.usage.total_tokens == 14 and final.meta.request_id == "gen-1"
    sent = json.loads(route.calls[0].request.content)
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize(("cls", "base", "fmt"), CASES, ids=IDS)
@respx.mock
async def test_async_paths(cls, base, fmt) -> None:
    respx.post(f"{base}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=completion("async")),
            httpx.Response(200, content=sse(chunk({"content": "a"}), chunk({}, finish="stop"))),
        ]
    )
    a = make(cls)
    assert (await a.agenerate([ChatMessage.user("x")])).content == "async"
    events = [e async for e in a.astream([ChatMessage.user("x")])]
    assert events[-1].response.content == "a"
    await a.aclose()


class Answer(BaseModel):
    city: str
    temp: int


@pytest.mark.parametrize(("cls", "base", "fmt"), CASES, ids=IDS)
@respx.mock
def test_structured_output_mode(cls, base, fmt) -> None:
    route = respx.post(f"{base}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('```json\n{"city": "Tokyo", "temp": 20}\n```'))
    )
    res = make(cls).generate([ChatMessage.user("weather")], response_schema=Answer)
    assert res.parsed == Answer(city="Tokyo", temp=20)
    body = json.loads(route.calls[0].request.content)
    if fmt == "json_schema":
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["schema"]["required"] == ["city", "temp"]
    else:
        assert body["response_format"] == {"type": "json_object"}
    # どの方式でもスキーマを指示文で添える（response_format を黙って捨てるサーバーがあるため）
    assert body["messages"][0]["role"] == "system" and '"city"' in body["messages"][0]["content"]


@respx.mock
def test_response_format_overrides() -> None:
    route = respx.post(f"{LOCAL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('{"city": "Tokyo", "temp": 1}'))
    )
    a = make(OpenAICompatibleChatAdapter, response_format="prompt")
    a.generate([ChatMessage.user("w")], response_schema=Answer)
    a.generate(
        [ChatMessage.user("w")],
        response_schema=Answer,
        **{
            "openai-compatible": {
                "response_format": "json_object",
                "seed": 7,
                "extra_body": {"cache_prompt": True},
            }
        },
    )
    first, second = (json.loads(c.request.content) for c in route.calls)
    assert "response_format" not in first and first["messages"][0]["role"] == "system"
    assert second["response_format"] == {"type": "json_object"}
    assert second["seed"] == 7 and second["cache_prompt"] is True


ERRORS = [
    (
        400,
        {
            "error": {
                "message": "This model's maximum context length is 8192 tokens",
                "code": "context_length_exceeded",
            }
        },
        ContextLengthExceededError,
        False,
    ),
    (400, {"error": {"message": "bad"}}, InvalidRequestError, False),
    (401, {"error": "Incorrect API key provided"}, AuthenticationError, False),
    (402, {"error": {"message": "Insufficient credits", "code": 402}}, QuotaExceededError, False),
    (404, {"error": {"message": "model not found"}}, NotFoundError, False),
    (429, {"error": {"message": "slow down"}}, RateLimitError, True),
    (503, {"error": {"message": "upstream overloaded"}}, ProviderOverloadedError, True),
]


@pytest.mark.parametrize(("status", "body", "exc", "retryable"), ERRORS)
@respx.mock
def test_error_mapping(status: int, body: dict, exc: type[OmniError], retryable: bool) -> None:
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(status, json=body, headers={"retry-after": "1"})
    )
    with pytest.raises(exc) as info:
        make(OpenRouterChatAdapter).generate([ChatMessage.user("x")])
    assert info.value.provider == "openrouter" and info.value.retryable is retryable


@respx.mock
def test_in_band_errors() -> None:
    url = "https://openrouter.ai/api/v1/chat/completions"
    # 200 のまま本文にエラー
    respx.post(url).mock(return_value=httpx.Response(200, json={"error": {"message": "no", "code": 502}}))
    with pytest.raises(ProviderOverloadedError):
        make(OpenRouterChatAdapter, retry=RetryPolicy(max_attempts=1)).generate([ChatMessage.user("x")])
    # ストリームの途中でエラー（出力の後なら中断として扱う）
    respx.post(url).mock(
        return_value=httpx.Response(
            200, content=sse(chunk({"content": "par"}), {"error": {"message": "boom"}})
        )
    )
    with pytest.raises(StreamInterruptedError):
        list(make(OpenRouterChatAdapter).stream([ChatMessage.user("x")]))


@respx.mock
def test_stream_without_finish_is_error_and_without_done_is_ok() -> None:
    url = f"{LOCAL}/v1/chat/completions"
    respx.post(url).mock(return_value=httpx.Response(200, content=sse(chunk({"content": "x"}), done=False)))
    with pytest.raises(StreamInterruptedError):
        list(make(OpenAICompatibleChatAdapter).stream([ChatMessage.user("x")]))
    respx.post(url).mock(
        return_value=httpx.Response(
            200, content=sse(chunk({"content": "x"}), chunk({}, finish="length"), done=False)
        )
    )
    events = list(make(OpenAICompatibleChatAdapter).stream([ChatMessage.user("x")]))
    assert events[-1].finish_reason == "length"


@respx.mock
def test_images() -> None:
    route = respx.post(f"{LOCAL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion())
    )
    content = [
        TextPart(text="what is this"),
        MediaPart(type="image", source=b"\x89PNG", mime_type="image/png"),
        MediaPart(type="image", source=UrlRef(url="https://example.com/a.png"), mime_type="image/png"),
    ]
    make(OpenAICompatibleChatAdapter).generate([ChatMessage.user(content)])
    parts = json.loads(route.calls[0].request.content)["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[2]["image_url"]["url"] == "https://example.com/a.png"
    with pytest.raises(UnsupportedFeatureError):
        make(OpenAICompatibleChatAdapter).generate(
            [ChatMessage.user([MediaPart(type="audio", source=b"x", mime_type="audio/wav")])]
        )


# ---------------------------------------------------------------------------- 接続先・キー


def test_keys_from_env_and_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AuthenticationError, match="XAI_API_KEY"):
        XAIChatAdapter(default_model="m")
    monkeypatch.setenv("XAI_API_KEY", "from-env")
    assert XAIChatAdapter()._sync.headers["Authorization"] == "Bearer from-env"
    (tmp_path / "secrets.toml").write_text('[openrouter]\napi_key = "from-file"\n')
    assert OpenRouterChatAdapter()._sync.headers["Authorization"] == "Bearer from-file"
    monkeypatch.setenv("OMNI_OPENROUTER_API_KEY", "omni-env")  # OMNI_ が各社の変数・ファイルより優先
    assert OpenRouterChatAdapter()._sync.headers["Authorization"] == "Bearer omni-env"


def test_openai_compatible_needs_url_and_key_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(InvalidRequestError, match="base_url"):
        OpenAICompatibleChatAdapter()
    a = OpenAICompatibleChatAdapter(base_url="http://127.0.0.1:8080/v1/")
    assert a.base_url == f"{LOCAL}/v1" and "Authorization" not in a._sync.headers
    monkeypatch.setenv("OMNI_OPENAI_COMPATIBLE_BASE_URL", "http://gpu-box:8000")
    assert OpenAICompatibleChatAdapter().base_url == "http://gpu-box:8000/v1"


def test_builtins_rejected_and_model_required() -> None:
    with pytest.raises(UnsupportedFeatureError, match="ToolRunner"):
        make(XAIChatAdapter).generate([ChatMessage.user("x")], tools=[BuiltinTool.WEB_SEARCH])
    with pytest.raises(InvalidRequestError, match="model is required"):
        XAIChatAdapter(api_key="k").generate([ChatMessage.user("x")])


# ---------------------------------------------------------------------------- Ollama Cloud


@respx.mock
def test_ollama_cloud_native_api(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AuthenticationError, match="OLLAMA_API_KEY"):
        OllamaCloudChatAdapter(default_model="gpt-oss:120b")
    monkeypatch.setenv("OLLAMA_API_KEY", "oc")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")  # ローカルの設定に引きずられない
    route = respx.post("https://ollama.com/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "gpt-oss:120b",
                "message": {"role": "assistant", "content": "OK"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 1,
            },
        )
    )
    a = OllamaCloudChatAdapter(default_model="gpt-oss:120b")
    res = a.generate([ChatMessage.user("hi")], tools=[get_weather])  # /api/show での能力確認はしない
    assert res.content == "OK" and res.meta.provider == "ollama-cloud"
    assert route.calls[0].request.headers["authorization"] == "Bearer oc"
    respx.post("https://ollama.com/api/chat").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    with pytest.raises(AuthenticationError) as info:
        a.generate([ChatMessage.user("hi")])
    assert info.value.provider == "ollama-cloud"


def test_pricing_treats_cloud_providers_as_paid() -> None:
    from omni_ai.types import ResponseMeta, Usage

    table = PricingTable({("xai", "grok-4"): ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0)})
    usage = Usage(prompt_tokens=1_000_000, completion_tokens=0)
    assert table.estimate(ResponseMeta(provider="xai", model="grok-4", usage=usage)) == 3.0
    for provider in ("ollama-cloud", "openai-compatible", "openrouter"):
        assert table.estimate(ResponseMeta(provider=provider, model="m", usage=usage)) is None


@respx.mock
async def test_fallback_across_new_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "k")
    respx.post("https://api.x.ai/v1/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "down"})
    )
    respx.post(f"{LOCAL}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion("local"))
    )
    from omni_ai.router import FallbackChatAdapter

    chat = FallbackChatAdapter(
        [
            make(XAIChatAdapter, retry=RetryPolicy(max_attempts=1)),
            make(OpenAICompatibleChatAdapter),
        ]
    )
    ai = OmniClient(chat=chat)
    res = await ai.chat.agenerate([ChatMessage.user("x")])
    assert res.content == "local" and res.meta.provider == "openai-compatible"
    assert res.meta.fallback_from == ["xai"]
    await ai.aclose()
