"""Ollama ＋ MCP ツールの end-to-end。OMNI_LIVE_OLLAMA=<model> のときだけ実行する（mcp が必要）。"""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from omni_ai import ChatMessage, OmniClient
from omni_ai.providers.mcp import MCPClientAdapter
from omni_ai.providers.ollama import OllamaChatAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OMNI_LIVE_OLLAMA"), reason="set OMNI_LIVE_OLLAMA=<model>"),
]
SERVER = Path(__file__).parent.parent / "fixtures" / "mcp" / "demo_server.py"


async def test_live_ollama_calls_mcp_tool(tmp_path: Path) -> None:
    async with MCPClientAdapter(
        "demo", command=[sys.executable, str(SERVER)], errlog=tmp_path / "e.log"
    ) as mcp:
        tools = await mcp.atools()
        async with OmniClient(chat=OllamaChatAdapter(default_model=os.environ["OMNI_LIVE_OLLAMA"])) as ai:
            res = await ai.tools.arun(
                [
                    ChatMessage.user(
                        "What's the weather in Tokyo and in Osaka? Use the weather tool for each city."
                    )
                ],
                [t for t in tools if t.name == "weather"],
                ollama={"think": False},
            )
    text = (res.response.content or "").lower()
    assert "20" in text and "15" in text, text
