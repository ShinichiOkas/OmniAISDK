"""mlx-lm の実物テスト。Apple Silicon の macOS で、mlx-lm が入っていて、
OMNI_LIVE_MLX_MODEL（HF のリポジトリ名かディレクトリパス）があるときだけ実行する。

例: OMNI_LIVE_MLX_MODEL=~/models/gemma-4-e2b-it-4bit（または mlx-community/gemma-4-e2b-it-4bit）
"""

import importlib.util
import os

import pytest

from omni_ai import ChatMessage, OmniClient
from omni_ai.providers.mlx_lm import MLXLMChatAdapter, is_supported_platform

MODEL = os.environ.get("OMNI_LIVE_MLX_MODEL", "")
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not is_supported_platform(), reason="mlx-lm runs only on Apple Silicon macOS"),
    pytest.mark.skipif(importlib.util.find_spec("mlx_lm") is None, reason='pip install "omni-ai-sdk[mlx]"'),
    pytest.mark.skipif(not MODEL, reason="set OMNI_LIVE_MLX_MODEL=<repo id or directory>"),
]


async def test_live_generate_stream_and_think_switch() -> None:
    async with OmniClient(chat=MLXLMChatAdapter(default_model=MODEL)) as ai:
        off = await ai.chat.agenerate(
            [ChatMessage.user("日本の首都はどこ？一言で。")], max_tokens=256, **{"mlx-lm": {"think": False}}
        )
        assert "東京" in (off.content or "") and off.reasoning is None
        assert off.meta.usage.prompt_tokens > 0 and off.meta.usage.completion_tokens > 0
        assert off.finish_reason == "stop"

        evs = [e async for e in ai.chat.astream([ChatMessage.user("1 から 3 まで数えて。")], max_tokens=512)]
        assert evs[-1].type == "message_complete" and evs[-1].response is not None
        text = "".join(e.delta_text for e in evs if e.type == "text_delta")
        assert text == evs[-1].response.content and "3" in text
        for marker in ("<|channel>", "<channel|>", "<think>", "</think>"):
            assert marker not in text

        short = await ai.chat.agenerate(
            [ChatMessage.user("1 から 30 まで数えて。")], max_tokens=4, **{"mlx-lm": {"think": False}}
        )
        assert short.finish_reason == "length"
