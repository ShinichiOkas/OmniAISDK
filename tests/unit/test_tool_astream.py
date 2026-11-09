"""ToolRunner.astream — ストリームしながらツールを回す。"""

from __future__ import annotations

import pytest

from omni_ai.exceptions import ToolLoopLimitError
from omni_ai.tools import ToolRunner
from omni_ai.types import ChatMessage, StreamEvent, ToolCall, omni_tool
from tests.fakes import FakeChat, reply, stream_of


@omni_tool
def get_weather(city: str) -> str:
    """Get weather.

    Args:
        city: city name
    """
    return f"sunny in {city}"


def _stream_tool_call(call: ToolCall, provider: str = "fake") -> list[StreamEvent]:
    res = reply(calls=[call], provider=provider)
    return [
        StreamEvent(type="tool_call", tool_call=call),
        StreamEvent(type="message_complete", finish_reason="tool_calls", response=res, meta=res.meta),
    ]


@pytest.mark.asyncio
async def test_astream_runs_tool_and_emits_result():
    call = ToolCall(id="c1", name="get_weather", arguments={"city": "Tokyo"})
    chat = FakeChat(
        "fake",
        script=[
            _stream_tool_call(call),
            stream_of("It's sunny in Tokyo."),
        ],
    )
    runner = ToolRunner(chat)
    events = [
        ev async for ev in runner.astream([ChatMessage.user("weather?")], [get_weather])
    ]
    kinds = [e.type for e in events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    tool_result = next(e for e in events if e.type == "tool_result")
    assert tool_result.tool_name == "get_weather"
    assert tool_result.tool_output is not None and "sunny" in tool_result.tool_output
    assert kinds[-1] == "turn_complete"
    # ふだんの message_complete も流れる
    assert kinds.count("message_complete") == 2


@pytest.mark.asyncio
async def test_astream_no_tools_completes_in_one_turn():
    chat = FakeChat("fake", script=[stream_of("Hello.")])
    runner = ToolRunner(chat)
    events = [ev async for ev in runner.astream([ChatMessage.user("hi")], [])]
    kinds = [e.type for e in events]
    assert "text_delta" in kinds
    assert kinds[-1] == "turn_complete"


@pytest.mark.asyncio
async def test_astream_max_turns_raises_when_summary_disabled():
    # ずっとツールを呼び続ける
    call = ToolCall(id="c1", name="get_weather", arguments={"city": "Tokyo"})
    chat = FakeChat(
        "fake",
        script=[_stream_tool_call(call) for _ in range(5)],
    )
    runner = ToolRunner(chat, max_turns=2)
    with pytest.raises(ToolLoopLimitError):
        _ = [ev async for ev in runner.astream([ChatMessage.user("weather?")], [get_weather])]


@pytest.mark.asyncio
async def test_astream_max_turns_summary_emits_closing_text():
    call = ToolCall(id="c1", name="get_weather", arguments={"city": "Tokyo"})
    chat = FakeChat(
        "fake",
        script=[
            _stream_tool_call(call),
            _stream_tool_call(call),
            stream_of("It's been sunny."),  # 要約として流れる
        ],
    )
    runner = ToolRunner(chat, max_turns=2)
    events = [
        ev
        async for ev in runner.astream(
            [ChatMessage.user("weather?")], [get_weather], on_max_turns_summary=True
        )
    ]
    kinds = [e.type for e in events]
    assert "max_turns" in kinds
    # max_turns イベントの後に要約の text_delta が出ている
    idx = kinds.index("max_turns")
    assert "text_delta" in kinds[idx + 1 :]
    assert kinds[-1] == "turn_complete"
