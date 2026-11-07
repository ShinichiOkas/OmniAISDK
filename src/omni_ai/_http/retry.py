"""再試行ポリシー（ADR-08）。待ち時間の計算は純関数にして同期・非同期で共有する。"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..exceptions import OmniError, RateLimitError

__all__ = ["RetryPolicy"]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2  # 初回を含む
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25  # ±25%
    respect_retry_after: bool = True

    def next_delay(self, attempt: int, error: OmniError, rng: random.Random | None = None) -> float | None:
        """``attempt`` 回目（1 始まり）が ``error`` で失敗したときの待ち秒数。再試行しないなら None。"""
        if not error.retryable or attempt >= self.max_attempts:
            return None
        if (
            self.respect_retry_after
            and isinstance(error, RateLimitError)
            and error.retry_after_seconds is not None
        ):
            if error.retry_after_seconds > self.max_delay:
                return None  # 待つ価値が無い → 呼び出し側（フォールバック）に任せる
            return max(0.0, error.retry_after_seconds)
        delay = min(self.max_delay, self.base_delay * (2.0 ** (attempt - 1)))
        factor: float = (rng or random).uniform(-self.jitter, self.jitter)
        return max(0.0, delay * (1 + factor))


NO_RETRY = RetryPolicy(max_attempts=1)
