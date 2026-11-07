"""MCPClientAdapter の契約テスト。tests/fixtures/mcp/demo_server.py を stdio で実際に起動して確かめる。"""

import base64
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from omni_ai import (
    ChatMessage,
    OmniError,
    ProviderConnectionError,
    ProviderTimeoutError,
)
from omni_ai.providers.mcp import MCPClientAdapter, describe_exc
from omni_ai.tools import ToolRunner
from omni_ai.types import ToolCall
from tests.fakes import FakeChat, reply

SERVER = Path(__file__).parent.parent / "fixtures" / "mcp" / "demo_server.py"


def demo(tmp_path: Path, **kw) -> MCPClientAdapter:
    return MCPClientAdapter(
        "demo", command=[sys.executable, str(SERVER)], errlog=tmp_path / "demo.stderr.log", **kw
    )


async def test_list_and_call_tools(tmp_path: Path) -> None:
    async with demo(tmp_path) as mcp:
        tools = {t.name: t for t in await mcp.alist_tools()}
        assert set(tools) == {"add", "weather", "fail", "pixel", "slow"}
        schema = tools["add"].parameters_schema
        assert "title" not in schema and schema["required"] == ["a", "b"]
        res = await mcp.acall_tool("add", {"a": 2, "b": 3})
        assert (res.text, res.is_error, res.structured) == ("5", False, {"result": 5})


async def test_tool_errors_are_results_not_exceptions(tmp_path: Path) -> None:
    log = tmp_path / "demo.stderr.log"
    async with demo(tmp_path) as mcp:
        failed = await mcp.acall_tool("fail", {"reason": "x"})
        unknown = await mcp.acall_tool("nope", {})
        invalid = await mcp.acall_tool("add", {"a": "x", "b": 1})
    assert failed.is_error and unknown.is_error and invalid.is_error
    assert '"error"' in failed.for_model()
    # サーバーの traceback は親の stderr ではなくログファイルへ（捨てない）
    assert "failed on purpose" in log.read_text(encoding="utf-8")


async def test_image_is_attachment_not_text(tmp_path: Path) -> None:
    async with demo(tmp_path) as mcp:
        res = await mcp.acall_tool("pixel", {})
    assert res.text == "" and len(res.attachments) == 1
    att = res.attachments[0]
    assert att.kind == "image" and att.mime_type == "image/png" and base64.b64decode(att.data_base64 or "")
    assert att.data_base64 not in res.for_model() and "attachment: image" in res.for_model()


async def test_call_timeout(tmp_path: Path) -> None:
    async with demo(tmp_path) as mcp:
        with pytest.raises(ProviderTimeoutError):
            await mcp.acall_tool("slow", {"seconds": 3}, timeout=0.5)


async def test_prefix_and_not_connected(tmp_path: Path) -> None:
    mcp = demo(tmp_path, tool_prefix="demo__")
    with pytest.raises(OmniError, match="not connected"):
        await mcp.alist_tools()
    async with mcp:
        names = [t.name for t in await mcp.alist_tools()]
        assert "demo__add" in names
        assert (await mcp.acall_tool("demo__add", {"a": 1, "b": 1})).text == "2"


async def test_bad_command_is_connection_error(tmp_path: Path) -> None:
    mcp = MCPClientAdapter("missing", command=["omni-no-such-command-xyz"], errlog=tmp_path / "m.log")
    with pytest.raises(ProviderConnectionError):
        await mcp.connect()


def test_constructor_validation() -> None:
    with pytest.raises(ValueError):
        MCPClientAdapter("x")
    with pytest.raises(ValueError):
        MCPClientAdapter("x", command=["a"], url="http://x")


def test_describe_exc_unwraps_groups() -> None:
    eg = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionRefusedError(61, "refused")])
    assert describe_exc(eg) == "connection refused — the server is not running"


async def test_tool_runner_uses_mcp_tools(tmp_path: Path) -> None:
    async with demo(tmp_path) as mcp:
        tools = await mcp.atools()
        chat = FakeChat(
            "ollama",
            [
                reply(
                    calls=[
                        ToolCall(id="c1", name="weather", arguments={"city": "Tokyo"}),
                        ToolCall(id="c2", name="fail", arguments={"reason": "r"}),
                    ]
                ),
                reply("done"),
            ],
        )
        res = await ToolRunner(chat).arun([ChatMessage.user("q")], tools)
    assert res.response.content == "done"
    tool_msgs = [m for m in res.messages if m.role.value == "tool"]
    assert tool_msgs[0].content == "sunny, 20C" and '"error"' in tool_msgs[1].content
