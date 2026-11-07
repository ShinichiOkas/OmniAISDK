"""全モダリティ共通のテレメトリ型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

__all__ = ["FinishReason", "ResponseMeta", "Usage"]

FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error", "unknown"]


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


class ResponseMeta(BaseModel):
    """全モダリティ共通の追跡・コスト計算メタデータ。"""

    provider: str
    model: str
    latency_ms: float = 0.0
    time_to_first_event_ms: float | None = None
    # 単価不明なら None。0 と区別する (ADR-10)
    estimated_cost_usd: float | None = None
    request_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    attempts: int = 1
    fallback_from: list[str] = Field(default_factory=list)
