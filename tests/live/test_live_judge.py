"""Ollama の decision モデル。OMNI_LIVE_OLLAMA_JUDGE=<model,...> のときだけ実行する。

例: OMNI_LIVE_OLLAMA_JUDGE=tev1:0.8b,kev:0.8b-q8_0
"""

import os

import pytest

from omni_ai.providers.ollama.judge import OllamaJudgeAdapter
from omni_ai.types import ChoiceQuestion, NoulQuestion, ScoreQuestion

MODELS = [m for m in os.environ.get("OMNI_LIVE_OLLAMA_JUDGE", "").split(",") if m]
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not MODELS, reason="set OMNI_LIVE_OLLAMA_JUDGE=<model,...>"),
]


@pytest.mark.parametrize("model", MODELS or ["-"])
async def test_live_judge(model: str) -> None:
    j = OllamaJudgeAdapter(default_model=model)
    grounded = await j.aevaluate(
        {"query": "猫の睡眠時間は？", "context": "猫は1日に12〜16時間眠る。"},
        {"ok": NoulQuestion(instructions="コンテキストに回答の根拠が含まれているか")},
    )
    ungrounded = await j.aevaluate(
        {"query": "猫の睡眠時間は？", "context": "株価は本日急落した。"},
        {"ok": NoulQuestion(instructions="コンテキストに回答の根拠が含まれているか")},
    )
    assert grounded.verdicts["ok"].probability > ungrounded.verdicts["ok"].probability
    res = await j.aevaluate(
        "The production database is down and all users see errors.",
        {
            "severity": ChoiceQuestion(
                instructions="Severity", criteria={"low": "cosmetic", "high": "service impacting"}
            ),
            "urgency": ScoreQuestion(instructions="Urgency", criteria=["Routine", "Soon", "Immediate"]),
        },
    )
    assert res.verdicts["severity"].value == "high"
    assert 0.0 <= float(res.verdicts["urgency"].value) <= 2.0
    await j.aclose()
