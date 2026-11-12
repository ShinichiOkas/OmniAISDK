"""ネットワーク由来の縮退を一元に扱う部品。

- ``StreamIdleError``: 最初のチャンクのあと、上限を超えて次のチャンクが来なかった
- ``UpstreamUnreachableError``: 上流が死んでいるときの冷却中の呼び出しを、ネットワークに出さずに断った
- ``DeadUpstreamGate``: 接続エラーの直後 ``cooldown`` 秒のあいだ同じプロバイダの呼び出しを撃たない門

**なぜこれが要るか（実運用の実測）**

- 失敗したターンの後に背景の呼び出し（自動命名・グラフ構築・スキル自動検出など）が、落ちた上流を 1 本ずつ
  叩き続ける。SDK の再試行込みで 1 呼び出し 5〜15 秒の待ちが積まれる
  （票 20261108_Failed_Turn_Keeps_Calling_Dead_Upstream_From_Background_Steps）。
- Gemini でも同じ必要があるので、判定は差し替え可能にする
  （票 20261108_Gemini_Provider_Has_No_Dead_Upstream_Gate）。
- 既定の無音の上限は 60 秒・冷却は 30 秒。環境変数で変えられる
  （``OMNI_STREAM_IDLE_SECONDS`` ・ ``OMNI_DEAD_UPSTREAM_SECONDS``・どちらも 0 以下で無効）。

判定は既定で ``httpx`` の接続系例外。``openai`` SDK を使うアダプタは ``is_connection_error`` に
``openai.APIConnectionError`` 判定を渡せる。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import httpx

from .exceptions import OmniError, ProviderConnectionError

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

logger = logging.getLogger("omni_ai")

STREAM_IDLE_ENV = "OMNI_STREAM_IDLE_SECONDS"
STREAM_IDLE_TIMEOUT_SECONDS = 60.0
STREAM_IDLE_CODE = "llm_stream_idle"

DEAD_UPSTREAM_ENV = "OMNI_DEAD_UPSTREAM_SECONDS"
DEAD_UPSTREAM_COOLDOWN_SECONDS = 30.0


class StreamIdleError(OmniError):
    """最初のチャンクのあと、上限を超えて次のチャンクが来なかった。

    ``code`` は常に :data:`STREAM_IDLE_CODE`（アダプタの例外マップで拾いやすい）。
    """

    code = STREAM_IDLE_CODE

    def __init__(self, idle_seconds: float, chunks: int, *, provider: str = "") -> None:
        super().__init__(
            f"stream went silent for {idle_seconds:g}s after {chunks} chunk(s)",
            provider=provider,
        )
        self.idle_seconds = idle_seconds
        self.chunks = chunks


class UpstreamUnreachableError(ProviderConnectionError):
    """直前の接続エラーのあと cooldown の内にある呼び出しを、ネットワークに出さずに断った。

    ``ProviderConnectionError`` の子なので、接続エラーを前提にした既存の扱い
    （構造化出力の素の再呼び出しをしない等）がそのまま効く。
    """

    code = "dead_upstream"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number, using default %.1f", name, raw, default)
        return default


def stream_idle_seconds() -> float:
    """途中の無音の上限（秒）。0 以下は無効。"""
    return _env_float(STREAM_IDLE_ENV, STREAM_IDLE_TIMEOUT_SECONDS)


def dead_upstream_seconds() -> float:
    """門の cooldown（秒）。0 以下は無効。"""
    return _env_float(DEAD_UPSTREAM_ENV, DEAD_UPSTREAM_COOLDOWN_SECONDS)


_HTTPX_CONNECTION_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.NetworkError,
)


def is_httpx_connection_error(exc: BaseException) -> bool:
    """httpx での接続エラー判定（既定）。"""
    return isinstance(exc, _HTTPX_CONNECTION_EXCEPTIONS)


async def with_idle_timeout(
    source: AsyncIterator[Any],
    *,
    idle_seconds: float | None = None,
    provider: str = "",
) -> AsyncIterator[Any]:
    """``source`` の各チャンクに `idle_seconds` の待ち時間を課す。

    最初のチャンクには待ち時間を課さない（接続直後の遅延はよくある）。2 つ目以降は、上限を超えて
    次が来なければ :class:`StreamIdleError` を投げる。``idle_seconds`` 省略時は
    環境変数（``OMNI_STREAM_IDLE_SECONDS``）から読む。0 以下なら上限を課さない。
    """
    limit = idle_seconds if idle_seconds is not None else stream_idle_seconds()
    chunks = 0
    aiter = source.__aiter__()
    while True:
        try:
            if limit <= 0 or chunks == 0:
                item = await aiter.__anext__()
            else:
                with anyio.fail_after(limit):
                    item = await aiter.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError:
            raise StreamIdleError(limit, chunks, provider=provider) from None
        chunks += 1
        yield item


class DeadUpstreamGate:
    """接続エラーの直後 ``cooldown`` 秒のあいだ、同じプロバイダの呼び出しを撃たない門。

    - 時間が過ぎたら最初の 1 呼び出しだけが試し（probe）として本当に撃つ。その間に来た他は断られ続ける。
    - 試しが成功したら門は閉じ、失敗したらまた ``cooldown`` 秒断る。
    - 開ける条件は接続エラーだけ。他のエラー（HTTP 4xx/5xx・StreamIdle）では開けない。
    - 状態は :class:`DeadUpstreamGate` のインスタンスごと（別の宛先を巻き込まない）。
    - ``is_connection_error`` を差し替えれば判定を変えられる（openai SDK を使うアダプタ向け）。
    """

    def __init__(
        self,
        provider: str,
        *,
        cooldown_seconds: float | None = None,
        is_connection_error: Callable[[BaseException], bool] = is_httpx_connection_error,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._cooldown = cooldown_seconds if cooldown_seconds is not None else dead_upstream_seconds()
        self._is_connection_error = is_connection_error
        self._now = now
        self._lock = threading.Lock()
        self._open_until: float = 0.0   # この時刻までは門が開いている（通さない）
        self._probing: bool = False       # 試しが走っている最中は、他を断る

    @property
    def cooldown_seconds(self) -> float:
        return self._cooldown

    def check(self) -> None:
        """呼び出しの直前に呼ぶ。冷却中なら :class:`UpstreamUnreachableError` を投げる。

        冷却が過ぎていたら、**最初の 1 呼び出しだけ**が通る（試し）。
        """
        if self._cooldown <= 0:
            return
        with self._lock:
            now = self._now()
            if now < self._open_until:
                # まだ冷却中。probe の権利を取れる唯一のタイミング（冷却が過ぎた直後）ではない
                raise self._refuse("upstream in cooldown")
            if self._probing:
                raise self._refuse("upstream in cooldown (probe in progress)")
            # 冷却が過ぎたら 1 回だけ probe として通す
            if self._open_until > 0:
                self._probing = True

    def report(self, exc: BaseException | None) -> None:
        """呼び出しの結果を報告する。接続エラーなら冷却を始める。"""
        if self._cooldown <= 0:
            return
        with self._lock:
            self._probing = False
            if exc is not None and self._is_connection_error(exc):
                self._open_until = self._now() + self._cooldown
                logger.warning(
                    "%s: upstream looks dead (%s) — refusing calls for %.0fs (%s)",
                    self._provider,
                    type(exc).__name__,
                    self._cooldown,
                    DEAD_UPSTREAM_ENV,
                )
            else:
                self._open_until = 0.0

    def _refuse(self, message: str) -> UpstreamUnreachableError:
        return UpstreamUnreachableError(message, provider=self._provider)

    # コンテキストマネージャとして使うと、成功・失敗の報告を自動化できる
    def __enter__(self) -> DeadUpstreamGate:
        self.check()
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        self.report(exc)
