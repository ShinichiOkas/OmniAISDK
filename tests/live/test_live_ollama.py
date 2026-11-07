"""実 API テスト（Ollama）。``OMNI_LIVE_OLLAMA=<model>`` のときだけ実行する。

例: OMNI_LIVE_OLLAMA=gemma4:31b-cloud pytest -m live tests/live
"""

import os

import pytest
from pydantic import BaseModel

from omni_ai import (
    BuiltinTool,
    ChatMessage,
    FallbackChatAdapter,
    OmniClient,
    ProviderConnectionError,
    omni_tool,
)
from omni_ai.providers.gemini import GeminiChatAdapter
from omni_ai.providers.ollama import OllamaChatAdapter
from tests.fakes import FakeSearch

MODEL = os.environ.get("OMNI_LIVE_OLLAMA")
pytestmark = [pytest.mark.live, pytest.mark.skipif(not MODEL, reason="set OMNI_LIVE_OLLAMA=<model>")]


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city.

    Args:
        city: city name in English
    """
    return {"tokyo": "sunny, 20C", "osaka": "rainy, 15C"}.get(city.lower(), "unknown")


class Person(BaseModel):
    name: str
    age: int


async def test_live_tool_loop_and_telemetry() -> None:
    async with OmniClient(chat=OllamaChatAdapter(default_model=MODEL)) as ai:
        res = await ai.tools.arun(
            [ChatMessage.user("What's the weather in Tokyo and in Osaka? Use the tool for each city.")],
            [get_weather],
            ollama={"think": False},
        )
        text = (res.response.content or "").lower()
        assert "20" in text and "15" in text, text
        assert res.turns >= 2
        assert len(ai.telemetry.history) == res.turns
        assert all(m.latency_ms > 0 for m in ai.telemetry.history)


async def test_live_stream_and_schema() -> None:
    chat = OllamaChatAdapter(default_model=MODEL)
    events = [
        e
        async for e in chat.astream(
            [ChatMessage.user("Count from 1 to 5, separated by spaces.")], ollama={"think": False}
        )
    ]
    assert events[-1].type == "message_complete"
    assert "5" in "".join(e.delta_text for e in events if e.type == "text_delta")
    res = await chat.agenerate(
        [ChatMessage.user("Give a person named Ann aged 30 as JSON.")],
        response_schema=Person,
        ollama={"think": False},
    )
    assert res.parsed == Person(name="Ann", age=30)
    await chat.aclose()


async def test_live_fallback_from_unreachable_gemini() -> None:
    # Gemini を到達不能な URL に向け、接続失敗 → Ollama へ切り替わることを実物で確かめる
    gemini = GeminiChatAdapter(api_key="dummy", base_url="http://127.0.0.1:9/v1beta")
    async with OmniClient(chat=FallbackChatAdapter([gemini, OllamaChatAdapter(default_model=MODEL)])) as ai:
        res = await ai.chat.agenerate([ChatMessage.user("Reply with exactly: OK")], ollama={"think": False})
        assert res.meta.provider == "ollama" and res.meta.fallback_from == ["gemini"]
        assert ai.telemetry.unpriced_calls == 1  # :cloud モデルは単価不明


async def test_live_web_search_emulation() -> None:
    search = FakeSearch()
    async with OmniClient(chat=OllamaChatAdapter(default_model=MODEL), search=search) as ai:
        res = await ai.tools.arun(
            [ChatMessage.user("Search the web for 'OmniAI SDK' and summarize in one line.")],
            [BuiltinTool.WEB_SEARCH],
            ollama={"think": False},
        )
        assert search.queries, "model did not call web_search"
        assert res.response.content


def test_live_unreachable_ollama_is_connection_error() -> None:
    chat = OllamaChatAdapter(default_model=MODEL, base_url="http://127.0.0.1:9")
    with pytest.raises(ProviderConnectionError):
        chat.generate([ChatMessage.user("hi")])
