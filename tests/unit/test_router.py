import asyncio
import threading

import pytest

from omni_ai import (
    BuiltinTool,
    CapabilityNotConfiguredError,
    ChatMessage,
    InvalidRequestError,
    ProviderConnectionError,
    ProviderOverloadedError,
    QuotaExceededError,
    RateLimitError,
    UnsupportedFeatureError,
)
from omni_ai.pricing import ModelPrice, PricingTable
from omni_ai.router import FallbackChatAdapter, OmniClient, TelemetryTracker
from omni_ai.types import ResponseMeta, StreamEvent, Usage
from tests.fakes import FakeChat, reply, stream_of

MSG = [ChatMessage.user("hi")]


def over(p: str) -> ProviderOverloadedError:
    return ProviderOverloadedError("x", provider=p)


def test_fallback_on_retryable_and_records_path() -> None:
    a = FakeChat("gemini", [RateLimitError("rl", provider="gemini")])
    b = FakeChat("ollama", [reply("ok")])
    res = FallbackChatAdapter([a, b]).generate(MSG, model="gemini-3.8-flash")
    assert res.content == "ok" and res.meta.fallback_from == ["gemini"]
    assert a.calls[0]["model"] == "gemini-3.8-flash"
    assert b.calls[0]["model"] is None  # 別プロバイダに同じモデル名を渡さない


@pytest.mark.parametrize(
    "err", [QuotaExceededError("q", provider="gemini"), ProviderConnectionError("c", provider="gemini")]
)
def test_fallback_default_targets(err: Exception) -> None:
    res = FallbackChatAdapter([FakeChat("gemini", [err]), FakeChat("ollama", [reply("ok")])]).generate(MSG)
    assert res.content == "ok"


def test_no_fallback_on_invalid_request() -> None:
    b = FakeChat("ollama", [reply("ok")])
    with pytest.raises(InvalidRequestError):
        FallbackChatAdapter(
            [FakeChat("gemini", [InvalidRequestError("bad", provider="gemini")]), b]
        ).generate(MSG)
    assert b.calls == []


def test_model_map() -> None:
    b = FakeChat("ollama", [reply("ok")])
    FallbackChatAdapter(
        [FakeChat("gemini", [over("gemini")]), b],
        model_map={"ollama": {"gemini-3.8-flash": "gemma4:31b-cloud"}},
    ).generate(MSG, model="gemini-3.8-flash")
    assert b.calls[0]["model"] == "gemma4:31b-cloud"


def test_all_failed_raises_last_with_notes() -> None:
    with pytest.raises(ProviderOverloadedError) as ei:
        FallbackChatAdapter(
            [FakeChat("gemini", [over("gemini")]), FakeChat("ollama", [over("ollama")])]
        ).generate(MSG)
    assert ei.value.provider == "ollama"
    assert any("gemini failed" in n for n in ei.value.__notes__)


def test_builtin_tools_skip_unsupported_providers() -> None:
    a = FakeChat("ollama", [reply("never")])
    b = FakeChat("gemini", [reply("g")], builtins=frozenset({BuiltinTool.WEB_SEARCH}))
    fb = FallbackChatAdapter([a, b])
    assert fb.supported_builtins == frozenset({BuiltinTool.WEB_SEARCH})
    assert fb.generate(MSG, tools=[BuiltinTool.WEB_SEARCH]).content == "g"
    assert a.calls == []
    with pytest.raises(UnsupportedFeatureError):
        fb.generate(MSG, tools=[BuiltinTool.CODE_EXECUTION])


def test_stream_falls_back_only_before_first_event() -> None:
    a = FakeChat("gemini", [[over("gemini")]])
    b = FakeChat("ollama", [stream_of("hello")])
    events = list(FallbackChatAdapter([a, b]).stream(MSG))
    assert events[-1].response is not None and events[-1].response.meta.fallback_from == ["gemini"]

    mid = FakeChat("gemini", [[StreamEvent(type="text_delta", delta_text="He"), over("gemini")]])
    c = FakeChat("ollama", [stream_of("x")])
    it = FallbackChatAdapter([mid, c]).stream(MSG)
    assert next(it).delta_text == "He"
    with pytest.raises(ProviderOverloadedError):
        next(it)
    assert c.calls == []


async def test_async_fallback_and_stream() -> None:
    fb = FallbackChatAdapter(
        [
            FakeChat("gemini", [over("gemini"), [over("gemini")]]),
            FakeChat("ollama", [reply("ok"), stream_of("s")]),
        ]
    )
    assert (await fb.agenerate(MSG)).content == "ok"
    events = [e async for e in fb.astream(MSG)]
    assert events[0].delta_text == "s"


# ---------------------------------------------------------------- telemetry & pricing


def test_pricing_estimate() -> None:
    table = PricingTable(
        {
            ("gemini", "gemini-3.8-flash"): ModelPrice(
                input_per_mtok=1.0, output_per_mtok=4.0, cached_input_per_mtok=0.25
            )
        }
    )
    meta = ResponseMeta(
        provider="gemini",
        model="gemini-3.8-flash-001",
        usage=Usage(prompt_tokens=1000, cached_tokens=400, completion_tokens=100, reasoning_tokens=50),
    )
    assert table.estimate(meta) == pytest.approx((600 * 1 + 400 * 0.25 + 150 * 4) / 1e6)
    assert table.estimate(ResponseMeta(provider="gemini", model="unknown")) is None
    assert table.estimate(ResponseMeta(provider="ollama", model="qwen3:8b")) == 0.0
    assert table.estimate(ResponseMeta(provider="ollama", model="gemma4:31b-cloud")) is None


def test_client_instruments_and_hook_errors_are_swallowed() -> None:
    seen: list[ResponseMeta] = []

    def hook(meta: ResponseMeta) -> None:
        seen.append(meta)
        raise RuntimeError("hook bug")

    chat = FakeChat("ollama", [reply("a"), stream_of("b")], default_model="qwen3:8b")
    ai = OmniClient(chat=chat, on_response=hook)
    res = ai.chat.generate(MSG)
    assert res.meta.latency_ms > 0 and res.meta.estimated_cost_usd == 0.0
    events = list(ai.chat.stream(MSG))
    assert events[-1].meta is not None and events[-1].meta.time_to_first_event_ms is not None
    assert len(seen) == 2 and len(ai.telemetry.history) == 2
    assert ai.telemetry.by_provider()["ollama"]["calls"] == 2


def test_unpriced_calls_counted_separately() -> None:
    ai = OmniClient(chat=FakeChat("gemini", [reply("a")]))
    ai.chat.generate(MSG)
    assert ai.telemetry.total_cost_usd == 0.0 and ai.telemetry.unpriced_calls == 1


def test_tracker_thread_safe() -> None:
    t = TelemetryTracker()

    def work() -> None:
        for _ in range(500):
            t.record(ResponseMeta(provider="p", model="m", latency_ms=1.0))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(t.history) == 4000 and t.total_latency_ms == 4000.0


def test_client_unconfigured_and_close() -> None:
    ai = OmniClient()
    with pytest.raises(CapabilityNotConfiguredError):
        _ = ai.chat
    with pytest.raises(CapabilityNotConfiguredError):
        _ = ai.search
    chat = FakeChat("x", [])
    with OmniClient(chat=chat):
        pass
    assert chat.closed


async def test_client_aclose_closes_all_even_if_one_fails() -> None:
    class Bad(FakeChat):
        async def aclose(self) -> None:
            raise RuntimeError("boom")

    from tests.fakes import FakeSearch

    search = FakeSearch()
    closed: list[str] = []

    async def s_close() -> None:
        closed.append("search")

    search.aclose = s_close  # type: ignore[method-assign]
    with pytest.raises(ExceptionGroup):
        async with OmniClient(chat=Bad("x", []), search=search):
            pass
    assert closed == ["search"]


def test_asyncio_gather_parallel() -> None:
    async def main() -> list[str | None]:
        ai = OmniClient(chat=FakeChat("ollama", [reply("1"), reply("2")]))
        r = await asyncio.gather(ai.chat.agenerate(MSG), ai.chat.agenerate(MSG))
        return [x.content for x in r]

    assert sorted(asyncio.run(main())) == ["1", "2"]  # type: ignore[type-var]
