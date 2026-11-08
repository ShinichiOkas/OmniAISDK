"""実物テスト（OpenAI 互換サーバー）。``OMNI_LIVE_OPENAI_COMPATIBLE=<base_url>`` と
``OMNI_LIVE_OPENAI_COMPATIBLE_MODEL=<model>`` のときだけ実行する。

キー無しで試せる相手として、ローカルの Ollama の /v1 を使える:
OMNI_LIVE_OPENAI_COMPATIBLE=http://127.0.0.1:11434 OMNI_LIVE_OPENAI_COMPATIBLE_MODEL=gemma4:31b-cloud \
    pytest -m live tests/live/test_live_openai_compatible.py
"""

import os

import pytest
from pydantic import BaseModel

from omni_ai import ChatMessage, omni_tool
from omni_ai.providers.openai_compatible import OpenAICompatibleChatAdapter

BASE = os.environ.get("OMNI_LIVE_OPENAI_COMPATIBLE")
MODEL = os.environ.get("OMNI_LIVE_OPENAI_COMPATIBLE_MODEL")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (BASE and MODEL),
        reason="set OMNI_LIVE_OPENAI_COMPATIBLE=<base_url> and OMNI_LIVE_OPENAI_COMPATIBLE_MODEL=<model>",
    ),
]


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city.

    Args:
        city: city name in English
    """
    return {"tokyo": "sunny, 21C", "osaka": "rainy, 15C"}.get(city.lower(), "unknown")


class Answer(BaseModel):
    city: str
    temp_c: int


def adapter() -> OpenAICompatibleChatAdapter:
    return OpenAICompatibleChatAdapter(base_url=BASE, default_model=MODEL)


def test_text_and_usage() -> None:
    res = adapter().generate([ChatMessage.user("Reply with exactly: OK")])
    assert res.content and "OK" in res.content and res.finish_reason == "stop"
    assert res.meta.usage.prompt_tokens > 0


def test_tool_roundtrip_and_stream() -> None:
    a = adapter()
    msgs = [ChatMessage.user("What's the weather in Tokyo? Use the tool.")]
    res = a.generate(msgs, tools=[get_weather])
    assert res.finish_reason == "tool_calls" and res.tool_calls[0].arguments == {"city": "Tokyo"}
    msgs.append(ChatMessage(role="assistant", content="", tool_calls=res.tool_calls))
    msgs += [
        ChatMessage.tool(get_weather.invoke(t.arguments), tool_call_id=t.id, name=t.name)
        for t in res.tool_calls
    ]
    assert "21" in (a.generate(msgs, tools=[get_weather]).content or "")
    events = list(
        a.stream([ChatMessage.user("What's the weather in Osaka? Use the tool.")], tools=[get_weather])
    )
    assert [e.tool_call.arguments for e in events if e.type == "tool_call"] == [{"city": "Osaka"}]


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "prompt"])
def test_structured_output(mode: str) -> None:
    res = adapter().generate(
        [ChatMessage.user("Tokyo is 21 degrees C. Extract it.")],
        response_schema=Answer,
        **{"openai-compatible": {"response_format": mode}},
    )
    assert res.parsed == Answer(city="Tokyo", temp_c=21)
