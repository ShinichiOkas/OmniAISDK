import pytest

from omni_ai import (
    BuiltinTool,
    ChatMessage,
    Role,
    ToolCall,
    ToolDefinition,
    ToolLoopLimitError,
    UnsupportedFeatureError,
    omni_tool,
)
from omni_ai.router import OmniClient
from omni_ai.tools import ToolRunner
from tests.fakes import FakeChat, FakeSearch, reply

MSG = [ChatMessage.user("q")]


@omni_tool
def add(a: int, b: int) -> int:
    """Add."""
    return a + b


@omni_tool
async def slow_mul(a: int, b: int) -> int:
    """Multiply."""
    return a * b


def call(name: str, args: dict, cid: str) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args)


async def test_arun_executes_tools_and_appends_assistant_message() -> None:
    chat = FakeChat(
        "ollama",
        [
            reply(calls=[call("add", {"a": 1, "b": 2}, "c1"), call("slow_mul", {"a": 3, "b": 4}, "c2")]),
            reply("done"),
        ],
    )
    result = await ToolRunner(chat).arun(MSG, [add, slow_mul])
    assert result.response.content == "done" and result.turns == 2
    second = chat.calls[1]["messages"]
    assert [m.role for m in second] == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.TOOL]
    assert second[1].tool_calls and second[1].tool_calls[0].id == "c1"  # v3.0 で欠けていた assistant 発話
    assert [(m.tool_call_id, m.name, m.content) for m in second[2:]] == [
        ("c1", "add", "3"),
        ("c2", "slow_mul", "12"),
    ]
    assert result.messages[-1].content == "done"


def test_run_sync_handles_unknown_tool_and_bad_args() -> None:
    chat = FakeChat(
        "ollama",
        [
            reply(calls=[call("nope", {}, "c1"), call("add", {"a": "x", "b": 1}, "c2")]),
            reply("ok"),
        ],
    )
    ToolRunner(chat).run(MSG, [add])
    tool_msgs = chat.calls[1]["messages"][2:]
    assert "unknown tool" in tool_msgs[0].content
    assert "invalid arguments" in tool_msgs[1].content


def test_on_tool_error_raise() -> None:
    @omni_tool
    def boom() -> str:
        """Fail."""
        raise ValueError("x")

    chat = FakeChat("ollama", [reply(calls=[call("boom", {}, "c1")])])
    with pytest.raises(ValueError):
        ToolRunner(chat, on_tool_error="raise").run(MSG, [boom])


def test_max_turns() -> None:
    chat = FakeChat("ollama", [reply(calls=[call("add", {"a": 1, "b": 1}, f"c{i}")]) for i in range(3)])
    with pytest.raises(ToolLoopLimitError) as ei:
        ToolRunner(chat, max_turns=3).run(MSG, [add])
    assert ei.value.partial_result.turns == 3


async def test_builtin_web_search_emulated_for_ollama() -> None:
    search = FakeSearch()
    chat = FakeChat(
        "ollama", [reply(calls=[call("web_search", {"query": "AI 2026"}, "c1")]), reply("summary")]
    )
    res = await ToolRunner(chat, search=search).arun(MSG, [BuiltinTool.WEB_SEARCH])
    assert res.response.content == "summary" and search.queries == ["AI 2026"]
    sent_tools = chat.calls[0]["tools"]
    assert [t.name for t in sent_tools] == ["web_search"]
    assert "about AI 2026" in chat.calls[1]["messages"][-1].content


def test_builtin_native_passthrough_and_missing_capability() -> None:
    chat = FakeChat("gemini", [reply("grounded")], builtins=frozenset({BuiltinTool.WEB_SEARCH}))
    ToolRunner(chat).run(MSG, [BuiltinTool.WEB_SEARCH])
    assert chat.calls[0]["tools"] == [BuiltinTool.WEB_SEARCH]
    with pytest.raises(UnsupportedFeatureError):
        ToolRunner(FakeChat("ollama", [])).run(MSG, [BuiltinTool.CODE_EXECUTION])


def test_external_tool_returns_pending_calls() -> None:
    ext = ToolDefinition(
        name="ask_user", description="ask", parameters_schema={"type": "object", "properties": {}}
    )
    chat = FakeChat("ollama", [reply(calls=[call("ask_user", {}, "c1")])])
    res = ToolRunner(chat).run(MSG, [ext, add])
    assert [c.name for c in res.pending_tool_calls] == ["ask_user"]
    assert res.messages[-1].role is Role.ASSISTANT


async def test_client_tools_property_uses_search_and_instrumentation() -> None:
    search = FakeSearch()
    ai = OmniClient(
        chat=FakeChat(
            "ollama",
            [reply(calls=[call("web_search", {"query": "x"}, "c")]), reply("y")],
            default_model="qwen3:8b",
        ),
        search=search,
    )
    res = await ai.tools.arun(MSG, [BuiltinTool.WEB_SEARCH, add])
    assert res.response.content == "y"
    # chat 2 回 ＋ 代行した検索 1 回が、すべてテレメトリに載る
    assert [m.provider for m in ai.telemetry.history] == ["ollama", "fakesearch", "ollama"]
