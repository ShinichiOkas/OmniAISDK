"""実 API テスト（Gemini）。``OMNI_LIVE_GEMINI=1`` とキー（GEMINI_API_KEY 等）があるときだけ実行する。

モデルは ``OMNI_LIVE_GEMINI_MODEL``（既定 gemini-3.1-flash-lite）。2026-11-07 の実測で、
gemini-3.8-flash は同じキーで無応答のままタイムアウトした（実装設計書 R-08）。
"""

import os

import pytest

from omni_ai import BuiltinTool, ChatMessage, omni_tool
from omni_ai.providers.gemini import GeminiChatAdapter

MODEL = os.environ.get("OMNI_LIVE_GEMINI_MODEL", "gemini-3.1-flash-lite")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OMNI_LIVE_GEMINI"), reason="set OMNI_LIVE_GEMINI=1"),
]


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return "sunny, 20C"


def test_live_text() -> None:
    res = GeminiChatAdapter(default_model=MODEL).generate([ChatMessage.user("Reply with exactly: OK")])
    assert res.content and "OK" in res.content and res.meta.request_id


def test_live_parallel_tools_with_signature_roundtrip() -> None:
    chat = GeminiChatAdapter(default_model=MODEL)
    msgs = [ChatMessage.user("What's the weather in Tokyo and Osaka? Use the tool for each.")]
    res = chat.generate(msgs, tools=[get_weather])
    assert res.tool_calls
    history = [*msgs, res.to_message()]
    history += [
        ChatMessage.tool(get_weather(**c.arguments), tool_call_id=c.id, name=c.name) for c in res.tool_calls
    ]
    assert chat.generate(history, tools=[get_weather]).content


def test_live_stream_and_search() -> None:
    chat = GeminiChatAdapter(default_model=MODEL)
    events = list(chat.stream([ChatMessage.user("Count 1 to 3.")]))
    assert events[-1].type == "message_complete"
    res = chat.generate(
        [ChatMessage.user("What is the latest Python version?")], tools=[BuiltinTool.WEB_SEARCH]
    )
    assert res.content
