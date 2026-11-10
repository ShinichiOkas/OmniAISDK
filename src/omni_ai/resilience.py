"""ネットワーク由来の縮退の部品（公開の入口）。

実体は :mod:`omni_ai._resilience`。利用側（Mnemo など）は非公開の ``_`` 付きモジュールではなく、
ここから import する。``_resilience`` からの import も従来どおり動く（同じオブジェクト）。
"""

from __future__ import annotations

from ._resilience import (
    DEAD_UPSTREAM_COOLDOWN_SECONDS,
    DEAD_UPSTREAM_ENV,
    STREAM_IDLE_CODE,
    STREAM_IDLE_ENV,
    STREAM_IDLE_TIMEOUT_SECONDS,
    DeadUpstreamGate,
    StreamIdleError,
    UpstreamUnreachableError,
    is_httpx_connection_error,
    stream_idle_seconds,
    with_idle_timeout,
)

__all__ = [
    "DEAD_UPSTREAM_COOLDOWN_SECONDS",
    "DEAD_UPSTREAM_ENV",
    "STREAM_IDLE_CODE",
    "STREAM_IDLE_ENV",
    "STREAM_IDLE_TIMEOUT_SECONDS",
    "DeadUpstreamGate",
    "StreamIdleError",
    "UpstreamUnreachableError",
    "is_httpx_connection_error",
    "stream_idle_seconds",
    "with_idle_timeout",
]
