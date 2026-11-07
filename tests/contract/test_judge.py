"""OllamaJudgeAdapter の契約テスト（実物から記録した応答: fixtures/ollama_judge）。"""

import json

import httpx
import pytest
import respx

from omni_ai import InvalidRequestError, NotFoundError, OmniClient, UnsupportedFeatureError
from omni_ai._http import RetryPolicy
from omni_ai.providers.ollama.judge import OllamaJudgeAdapter
from omni_ai.types import ChoiceQuestion, NoulQuestion, ScoreQuestion

BASE = "http://ollama.test:11434"
URL = f"{BASE}/v1/systemone"
QUESTIONS = {
    "is_grounded": NoulQuestion(
        instructions="コンテキストに回答の根拠が含まれているか",
        criteria={"false": "根拠が無い", "true": "根拠がある"},
    ),
    "audience": ChoiceQuestion(
        instructions="最適な説明トーン",
        criteria={"executive": "経営層向け", "engineer": "技術者向け", "general": "一般向け"},
    ),
    "urgency": ScoreQuestion(instructions="対応の緊急度", criteria=["Routine", "Soon", "Immediate"]),
}
STATE = {"query": "猫の睡眠時間は？", "context": "猫は1日に12〜16時間眠る。"}


def judge() -> OllamaJudgeAdapter:
    return OllamaJudgeAdapter(base_url=BASE, default_model="tev1:0.8b", retry=RetryPolicy(max_attempts=1))


@respx.mock
def test_multi_question_mapping_and_wire(fixture_bytes) -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama_judge", "tev1_multi.json"))
    )
    res = judge().evaluate(STATE, QUESTIONS)
    # 送った要求が、実物で 200 になった要求と同じ形
    assert json.loads(route.calls[0].request.content) == json.loads(
        fixture_bytes("ollama_judge", "tev1_multi.request.json")
    )
    g, a, u = res.verdicts["is_grounded"], res.verdicts["audience"], res.verdicts["urgency"]
    assert g.value is True and g.probability == pytest.approx(0.8058, abs=1e-4)
    assert g.distribution and g.distribution["false"] == pytest.approx(1 - g.probability)
    assert a.value == "general" and a.probability == pytest.approx(0.8108, abs=1e-4) and a.confidence
    assert set(a.distribution or {}) == {"executive", "engineer", "general"}
    assert u.value == pytest.approx(0.9018, abs=1e-4)
    assert set(u.distribution or {}) == {"Routine", "Soon", "Immediate"}  # 段階名で引ける
    assert u.probability == pytest.approx(0.4493, abs=1e-4)
    assert (
        res.meta.provider == "ollama"
        and res.meta.usage.prompt_tokens == 440
        and res.meta.usage.cached_tokens == 150
    )


@pytest.mark.parametrize(
    ("name", "status", "exc"),
    [
        ("err_chat_model", 400, UnsupportedFeatureError),
        ("err_missing_model", 404, NotFoundError),
        ("err_bad_type", 400, InvalidRequestError),
    ],
)
@respx.mock
def test_recorded_errors(fixture_bytes, name: str, status: int, exc: type) -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(status, content=fixture_bytes("ollama_judge", f"{name}.json"))
    )
    with pytest.raises(exc):
        judge().evaluate("x", {"q": NoulQuestion(instructions="i")})


def test_client_side_validation() -> None:
    j = judge()
    with pytest.raises(InvalidRequestError):
        j.evaluate("x", {})
    with pytest.raises(InvalidRequestError):
        j.evaluate("  ", {"q": NoulQuestion(instructions="i")})
    with pytest.raises(InvalidRequestError, match="2-26"):
        j.evaluate("x", {"q": ChoiceQuestion(instructions="i", criteria={"only": "one"})})
    with pytest.raises(InvalidRequestError, match="2-10"):
        j.evaluate("x", {"q": ScoreQuestion(instructions="i", criteria=["one"])})
    with pytest.raises(InvalidRequestError, match="model is required"):
        OllamaJudgeAdapter(base_url=BASE).evaluate("x", {"q": NoulQuestion(instructions="i")})


@respx.mock
def test_missing_answer_is_error() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"model": "tev1:0.8b", "answers": {}}))
    with pytest.raises(Exception, match="no answer"):
        judge().evaluate("x", {"q": NoulQuestion(instructions="i")})


@respx.mock
async def test_async_and_telemetry(fixture_bytes) -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama_judge", "state_string.json"))
    )
    ai = OmniClient(judge=judge())
    res = await ai.judge.aevaluate(
        "HTTP 500 since deploy",
        {
            "severity": ChoiceQuestion(
                instructions="Severity", criteria={"low": "cosmetic", "high": "service impacting"}
            )
        },
    )
    assert res.verdicts["severity"].value == "high"
    assert ai.telemetry.history[0].provider == "ollama" and ai.telemetry.history[0].estimated_cost_usd == 0.0
    await ai.aclose()
