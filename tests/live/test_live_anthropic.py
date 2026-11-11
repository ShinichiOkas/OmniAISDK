"""Anthropic の実物テスト。ANTHROPIC_API_KEY と OMNI_LIVE_ANTHROPIC_MODEL があるときだけ実行する。"""

import os

import pytest

from omni_ai import ChatMessage, OmniClient
from omni_ai.providers.anthropic import AnthropicChatAdapter

MODEL = os.environ.get("OMNI_LIVE_ANTHROPIC_MODEL", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("ANTHROPIC_API_KEY") and MODEL),
        reason="set ANTHROPIC_API_KEY and OMNI_LIVE_ANTHROPIC_MODEL",
    ),
]


async def test_live_generate_and_stream() -> None:
    async with OmniClient(chat=AnthropicChatAdapter(default_model=MODEL)) as ai:
        r = await ai.chat.agenerate([ChatMessage.user("「こんにちは」とだけ返して。")], max_tokens=256)
        assert (r.content or "").strip()
        assert r.meta.usage.prompt_tokens > 0 and r.meta.usage.completion_tokens > 0
        msgs = [ChatMessage.user("1 から 3 まで数えて。")]
        chunks = [e async for e in ai.chat.astream(msgs, max_tokens=256)]
        assert chunks
