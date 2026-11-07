"""コスト推定（ADR-10）。単価表は同梱せず、利用者が注入する。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date

from pydantic import BaseModel

from .types import ResponseMeta

__all__ = ["ModelPrice", "PricingTable", "default_is_free"]


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None
    source: str = ""  # 単価の出典 URL
    as_of: date | None = None  # 取得日


def default_is_free(provider: str, model: str) -> bool:
    """ローカルの Ollama だけを無料とみなす（``:cloud`` / ``-cloud`` は ollama.com 上で動く）。"""
    return provider == "ollama" and not model.endswith((":cloud", "-cloud"))


class PricingTable:
    def __init__(
        self,
        prices: Mapping[tuple[str, str], ModelPrice] | None = None,
        *,
        is_free: Callable[[str, str], bool] = default_is_free,
    ) -> None:
        self.prices = dict(prices or {})
        self.is_free = is_free

    def lookup(self, provider: str, model: str) -> ModelPrice | None:
        """完全一致 → 前方一致（最長）で探す（例: ``gemini-3.8-flash-001`` → ``gemini-3.8-flash``）。"""
        if (provider, model) in self.prices:
            return self.prices[(provider, model)]
        best: tuple[int, ModelPrice] | None = None
        for (p, m), price in self.prices.items():
            if p == provider and model.startswith(m) and (best is None or len(m) > best[0]):
                best = (len(m), price)
        return best[1] if best else None

    def estimate(self, meta: ResponseMeta) -> float | None:
        if self.is_free(meta.provider, meta.model):
            return 0.0
        price = self.lookup(meta.provider, meta.model)
        if price is None:
            return None
        u = meta.usage
        cached = min(u.cached_tokens, u.prompt_tokens)
        cached_rate = (
            price.cached_input_per_mtok if price.cached_input_per_mtok is not None else price.input_per_mtok
        )
        cost = (
            (u.prompt_tokens - cached) * price.input_per_mtok
            + cached * cached_rate
            + (u.completion_tokens + u.reasoning_tokens) * price.output_per_mtok
        )
        return cost / 1_000_000
