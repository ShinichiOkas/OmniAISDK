"""StreamIdleError / DeadUpstreamGate の単体試験。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from omni_ai._resilience import (
    DeadUpstreamGate,
    StreamIdleError,
    UpstreamUnreachableError,
    with_idle_timeout,
)


@pytest.mark.asyncio
async def test_idle_timeout_fires_after_first_chunk():
    async def source():
        yield "a"
        await asyncio.sleep(0.5)
        yield "b"

    with pytest.raises(StreamIdleError) as info:
        async for _ in with_idle_timeout(source(), idle_seconds=0.1):
            pass
    assert info.value.chunks == 1


@pytest.mark.asyncio
async def test_idle_timeout_does_not_fire_before_first_chunk():
    async def source():
        await asyncio.sleep(0.2)
        yield "a"
        yield "b"

    items = [item async for item in with_idle_timeout(source(), idle_seconds=0.05)]
    assert items == ["a", "b"]


@pytest.mark.asyncio
async def test_idle_timeout_zero_disables():
    async def source():
        await asyncio.sleep(0.3)
        yield "a"

    items = [item async for item in with_idle_timeout(source(), idle_seconds=0)]
    assert items == ["a"]


def test_dead_upstream_gate_refuses_after_connection_error():
    clock = [100.0]

    def now():
        return clock[0]

    gate = DeadUpstreamGate("p", cooldown_seconds=30.0, now=now)
    # 最初はそのまま通る
    gate.check()
    err = httpx.ConnectError("boom")
    gate.report(err)
    # 冷却中は断る
    with pytest.raises(UpstreamUnreachableError):
        gate.check()
    # 29s 後もまだ断る
    clock[0] += 29.0
    with pytest.raises(UpstreamUnreachableError):
        gate.check()
    # 31s 後は試し（probe）として 1 回だけ通る
    clock[0] += 2.0
    gate.check()
    # probe が走っている間は他を断る
    with pytest.raises(UpstreamUnreachableError):
        gate.check()
    # probe が成功したら閉じる
    gate.report(None)
    gate.check()  # 次はまた通る


def test_dead_upstream_gate_ignores_non_connection_errors():
    gate = DeadUpstreamGate("p", cooldown_seconds=30.0)
    gate.check()
    gate.report(ValueError("not a connection error"))
    # 通常エラーでは門を閉めない
    gate.check()


def test_dead_upstream_gate_disabled_when_cooldown_zero():
    gate = DeadUpstreamGate("p", cooldown_seconds=0.0)
    gate.check()
    gate.report(httpx.ConnectError("boom"))
    gate.check()  # 0 のときは効かない


def test_dead_upstream_gate_context_manager():
    clock = [100.0]

    def now():
        return clock[0]

    gate = DeadUpstreamGate("p", cooldown_seconds=30.0, now=now)
    with pytest.raises(httpx.ConnectError):
        with gate:
            raise httpx.ConnectError("boom")
    # コンテキストマネージャ内で接続エラー → 冷却中
    with pytest.raises(UpstreamUnreachableError):
        with gate:
            pass
