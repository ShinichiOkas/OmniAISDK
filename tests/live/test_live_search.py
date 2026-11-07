"""実物の Web 検索（ddgs）。OMNI_LIVE_DDGS=1 のときだけ実行する。
OMNI_LIVE_OLLAMA=<model> も設定すると、Ollama ＋ 実検索の ToolRunner を end-to-end で確かめる。
"""

import os

import pytest

from omni_ai import BuiltinTool, ChatMessage, OmniClient
from omni_ai.providers.ddgs import DDGSSearchAdapter
from omni_ai.providers.ollama import OllamaChatAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OMNI_LIVE_DDGS"), reason="set OMNI_LIVE_DDGS=1"),
]


def test_live_ddgs_search() -> None:
    res = DDGSSearchAdapter().search("Python 公式サイト", max_results=5)
    assert res.results and any("python.org" in r.url for r in res.results)


@pytest.mark.skipif(not os.environ.get("OMNI_LIVE_OLLAMA"), reason="set OMNI_LIVE_OLLAMA=<model>")
async def test_live_ollama_web_search_via_tool_runner() -> None:
    async with OmniClient(
        chat=OllamaChatAdapter(default_model=os.environ["OMNI_LIVE_OLLAMA"]), search=DDGSSearchAdapter()
    ) as ai:
        res = await ai.tools.arun(
            [
                ChatMessage.user(
                    "Use web search to find the latest stable Python version, then answer in one line."
                )
            ],
            [BuiltinTool.WEB_SEARCH],
            ollama={"think": False},
        )
        providers = [m.provider for m in ai.telemetry.history]
        assert "ddgs" in providers and "ollama" in providers, providers
        assert res.response.content and "3." in res.response.content, res.response.content
