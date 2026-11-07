"""OllamaJudgeAdapter — Ollama の decision モデル（``POST /v1/systemone``）による型付き判定。

- 必要: Ollama v0.35.0 以上、``capabilities`` に ``decision`` を持つモデル
  （例: tev1:0.8b、kev:0.8b-q8_0、nimble）
- 質問の型: noul（はい／いいえの確率）・choice（2〜26 の選択肢）・score（2〜10 の段階）
- 一次資料: https://docs.ollama.com/capabilities/decision.md （2026-11-08 確認）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import AsyncTransport, RetryPolicy, SyncTransport
from ...capabilities import JudgeCapability
from ...compat import PlatformCompat
from ...config import resolve_setting
from ...exceptions import InvalidRequestError, OmniError, UnsupportedFeatureError
from ...types import (
    ChoiceQuestion,
    JudgeQuestion,
    JudgeResponse,
    NoulQuestion,
    ResponseMeta,
    ScoreQuestion,
    Usage,
    VerdictResult,
)
from . import OLLAMA_TIMEOUT, codec

__all__ = ["OllamaJudgeAdapter"]

_MAX_QUESTIONS = 64


def _question_wire(q: JudgeQuestion) -> dict[str, Any]:
    if isinstance(q, NoulQuestion):
        out: dict[str, Any] = {"type": "noul", "instructions": q.instructions}
        if q.criteria:
            out["criteria"] = dict(q.criteria)
        return out
    if isinstance(q, ChoiceQuestion):
        if not 2 <= len(q.criteria) <= 26:
            raise InvalidRequestError("choice criteria must have 2-26 options", provider="ollama")
        return {"type": "choice", "instructions": q.instructions, "criteria": dict(q.criteria)}
    if isinstance(q, ScoreQuestion):
        if not 2 <= len(q.criteria) <= 10:
            raise InvalidRequestError("score criteria must have 2-10 levels", provider="ollama")
        return {"type": "score", "instructions": q.instructions, "criteria": list(q.criteria)}
    raise TypeError(f"unsupported question: {q!r}")


def _verdict(answer: Mapping[str, Any], question: JudgeQuestion) -> VerdictResult:
    kind = answer.get("type")
    if kind == "noul":
        p = float(answer.get("noul", 0.0))
        return VerdictResult(value=p >= 0.5, probability=p, distribution={"true": p, "false": 1.0 - p})
    if kind == "choice":
        probs = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
        choice = str(answer.get("choice", ""))
        return VerdictResult(
            value=choice,
            probability=probs.get(choice, 0.0),
            distribution=probs,
            confidence=answer.get("confidence"),
        )
    if kind == "score":
        legend = {str(k): str(v) for k, v in (answer.get("legend") or {}).items()}
        raw = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
        if not legend and isinstance(question, ScoreQuestion):
            legend = {str(i): name for i, name in enumerate(question.criteria)}
        dist = {legend.get(k, k): v for k, v in raw.items()}
        return VerdictResult(
            value=float(answer.get("score", 0.0)),
            probability=max(raw.values(), default=0.0),
            distribution=dist,
            confidence=answer.get("confidence"),
        )
    raise OmniError(f"unknown answer type {kind!r}", provider="ollama")


class OllamaJudgeAdapter(JudgeCapability):
    provider_name: ClassVar[str] = "ollama"

    def __init__(
        self,
        *,
        default_model: str | None = None,
        base_url: str | None = None,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        setting = resolve_setting("ollama", "base_url", base_url)
        if setting.value is None:
            url = PlatformCompat.resolve_ollama_base_url()
        elif setting.source == "provider_env":
            url = PlatformCompat.normalize_ollama_host(setting.value)
        else:
            url = setting.value.rstrip("/")
        self.default_model = default_model
        common: dict[str, Any] = {
            "provider": self.provider_name,
            "base_url": url,
            "retry": retry,
            "timeout": timeout if timeout is not None else OLLAMA_TIMEOUT,
            "error_mapper": lambda s, b, h: self._map_error(s, b, h, None),
        }
        self._sync = SyncTransport(client=http_client, **common)
        self._async = AsyncTransport(client=async_http_client, **common)

    @staticmethod
    def _map_error(status: int, body: bytes, headers: Mapping[str, str], model: str | None) -> OmniError:
        err = codec.map_error(status, body, headers, model)
        if status == 400 and "not a decision model" in err.message:  # 実測
            return UnsupportedFeatureError(err.message, provider="ollama", model=model, status_code=400)
        return err

    def _body(
        self, state: str | dict[str, Any], questions: dict[str, JudgeQuestion], model: str | None
    ) -> tuple[str, dict[str, Any]]:
        resolved = model or self.default_model
        if not resolved:
            raise InvalidRequestError("model is required (e.g. tev1:0.8b)", provider=self.provider_name)
        if not 1 <= len(questions) <= _MAX_QUESTIONS:
            raise InvalidRequestError(
                f"questions must contain 1-{_MAX_QUESTIONS} entries", provider=self.provider_name
            )
        if isinstance(state, str) and not state.strip():
            raise InvalidRequestError("state is empty", provider=self.provider_name)
        return resolved, {
            "model": resolved,
            "state": state,
            "questions": {name: _question_wire(q) for name, q in questions.items()},
        }

    def _parse(
        self, data: Mapping[str, Any], questions: dict[str, JudgeQuestion], model: str, attempts: int
    ) -> JudgeResponse:
        answers = data.get("answers") or {}
        missing = [n for n in questions if n not in answers]
        if missing:
            raise OmniError(
                f"decision model returned no answer for {missing}", provider=self.provider_name, model=model
            )
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        return JudgeResponse(
            verdicts={name: _verdict(answers[name], q) for name, q in questions.items()},
            meta=ResponseMeta(
                provider=self.provider_name,
                model=model,  # 指定した名前（応答の model は版で変わる）
                attempts=attempts,
                usage=Usage(
                    prompt_tokens=tokens_in,
                    completion_tokens=tokens_out,
                    cached_tokens=int(data.get("prompt_eval_cached_count") or 0),
                    total_tokens=tokens_in + tokens_out,
                ),
            ),
        )

    def _mapper(self, model: str) -> Any:
        return lambda s, b, h: self._map_error(s, b, h, model)

    def evaluate(
        self, state: str | dict[str, Any], questions: dict[str, JudgeQuestion], *, model: str | None = None,
        **provider_options: Any,
    ) -> JudgeResponse:  # fmt: skip
        resolved, body = self._body(state, questions, model)
        r = self._sync.request("POST", "/v1/systemone", json=body, error_mapper=self._mapper(resolved))
        return self._parse(r.json(), questions, resolved, r.attempts)

    async def aevaluate(
        self, state: str | dict[str, Any], questions: dict[str, JudgeQuestion], *, model: str | None = None,
        **provider_options: Any,
    ) -> JudgeResponse:  # fmt: skip
        resolved, body = self._body(state, questions, model)
        r = await self._async.request("POST", "/v1/systemone", json=body, error_mapper=self._mapper(resolved))
        return self._parse(r.json(), questions, resolved, r.attempts)

    def close(self) -> None:
        self._sync.close()
        self._async.mark_closed_sync()

    async def aclose(self) -> None:
        await self._async.aclose()
        self._sync.close()
