"""ルーター層: 計測プロキシ・フォールバック・OmniClient（仕様書 v3.1 §6）。"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import aclosing
from types import TracebackType
from typing import TYPE_CHECKING, Any, ClassVar, Self

from .capabilities import (
    AudioCapability,
    Capability,
    ChatCapability,
    DocumentParserCapability,
    EmbeddingCapability,
    ImageCapability,
    JudgeCapability,
    MCPCapability,
    RerankCapability,
    SandboxCapability,
    SearchCapability,
)
from .exceptions import (
    CapabilityNotConfiguredError,
    OmniError,
    ProviderConnectionError,
    ProviderOverloadedError,
    QuotaExceededError,
    RateLimitError,
    UnsupportedFeatureError,
)
from .pricing import PricingTable
from .types import (
    BuiltinTool,
    ChatMessage,
    ChatResponse,
    EmbeddingResponse,
    ResponseMeta,
    StreamEvent,
    ToolSpec,
)

if TYPE_CHECKING:
    from .tools import ToolRunner

__all__ = ["DEFAULT_FALLBACK_ON", "FallbackChatAdapter", "OmniClient", "TelemetryTracker"]

logger = logging.getLogger("omni_ai")

DEFAULT_FALLBACK_ON: tuple[type[OmniError], ...] = (
    RateLimitError,
    ProviderOverloadedError,
    ProviderConnectionError,
    QuotaExceededError,
)


# ============================================================================ Telemetry


class TelemetryTracker:
    """複数プロバイダを跨いだ呼び出しの累計（スレッド安全）。"""

    def __init__(self) -> None:
        self.history: list[ResponseMeta] = []
        self._lock = threading.Lock()

    def record(self, meta: ResponseMeta) -> None:
        with self._lock:
            self.history.append(meta)

    @property
    def total_cost_usd(self) -> float:
        """単価既知の呼び出しの合計。単価不明 (None) は ``unpriced_calls`` で別に数える。"""
        with self._lock:
            return sum(m.estimated_cost_usd for m in self.history if m.estimated_cost_usd is not None)

    @property
    def unpriced_calls(self) -> int:
        with self._lock:
            return sum(1 for m in self.history if m.estimated_cost_usd is None)

    @property
    def total_latency_ms(self) -> float:
        with self._lock:
            return sum(m.latency_ms for m in self.history)

    def by_provider(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "cost_usd": 0.0, "latency_ms": 0.0}
        )
        with self._lock:
            for m in self.history:
                row = out[m.provider]
                row["calls"] += 1
                row["cost_usd"] += m.estimated_cost_usd or 0.0
                row["latency_ms"] += m.latency_ms
        return dict(out)


# ============================================================================ 計測プロキシ


class _Recorder:
    def __init__(
        self, tracker: TelemetryTracker, pricing: PricingTable, hook: Callable[[ResponseMeta], None] | None
    ) -> None:
        self.tracker = tracker
        self.pricing = pricing
        self.hook = hook

    def record(self, meta: ResponseMeta, started: float, first_event: float | None = None) -> None:
        meta.latency_ms = (time.perf_counter() - started) * 1000
        if first_event is not None:
            meta.time_to_first_event_ms = (first_event - started) * 1000
        meta.estimated_cost_usd = self.pricing.estimate(meta)
        self.tracker.record(meta)
        if self.hook is not None:
            try:
                self.hook(meta)
            except Exception:  # フックの失敗で本処理を失敗させない
                logger.warning("on_response hook raised; ignored", exc_info=True)


class InstrumentedChat(ChatCapability):
    """OmniClient.chat が返すプロキシ。時間・コストを記録してフックを呼ぶ。"""

    provider_name: ClassVar[str] = "instrumented"

    def __init__(self, inner: ChatCapability, recorder: _Recorder) -> None:
        self.inner = inner
        self._recorder = recorder
        self.supported_builtins = inner.supported_builtins
        self.default_model = inner.default_model

    @property
    def wrapped_provider(self) -> str:
        return self.inner.provider_name

    def generate(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        started = time.perf_counter()
        res = self.inner.generate(messages, **kwargs)
        self._recorder.record(res.meta, started)
        return res

    async def agenerate(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        started = time.perf_counter()
        res = await self.inner.agenerate(messages, **kwargs)
        self._recorder.record(res.meta, started)
        return res

    def stream(self, messages: Sequence[ChatMessage], **kwargs: Any) -> Iterator[StreamEvent]:
        started = time.perf_counter()
        first: float | None = None
        for ev in self.inner.stream(messages, **kwargs):
            if first is None:
                first = time.perf_counter()
            if ev.type == "message_complete" and ev.response is not None:
                self._recorder.record(ev.response.meta, started, first)
            yield ev

    async def astream(self, messages: Sequence[ChatMessage], **kwargs: Any) -> AsyncIterator[StreamEvent]:
        started = time.perf_counter()
        first: float | None = None
        async with aclosing(self.inner.astream(messages, **kwargs)) as inner:  # type: ignore[type-var]
            async for ev in inner:
                if first is None:
                    first = time.perf_counter()
                if ev.type == "message_complete" and ev.response is not None:
                    self._recorder.record(ev.response.meta, started, first)
                yield ev

    def close(self) -> None:
        self.inner.close()

    async def aclose(self) -> None:
        await self.inner.aclose()


class InstrumentedEmbedding(EmbeddingCapability):
    """OmniClient.embedding が返すプロキシ。時間・コストを記録してフックを呼ぶ。"""

    provider_name: ClassVar[str] = "instrumented"

    def __init__(self, inner: EmbeddingCapability, recorder: _Recorder) -> None:
        self.inner = inner
        self._recorder = recorder
        self.default_model = inner.default_model

    def embed(self, texts: str | Sequence[str], **kwargs: Any) -> EmbeddingResponse:
        started = time.perf_counter()
        res = self.inner.embed(texts, **kwargs)
        if res.vectors:
            self._recorder.record(res.meta, started)
        return res

    async def aembed(self, texts: str | Sequence[str], **kwargs: Any) -> EmbeddingResponse:
        started = time.perf_counter()
        res = await self.inner.aembed(texts, **kwargs)
        if res.vectors:
            self._recorder.record(res.meta, started)
        return res

    def close(self) -> None:
        self.inner.close()

    async def aclose(self) -> None:
        await self.inner.aclose()


# ============================================================================ Fallback


def _requested_builtins(tools: Sequence[ToolSpec] | None) -> set[BuiltinTool]:
    return {t for t in tools or () if isinstance(t, BuiltinTool)}


class FallbackChatAdapter(ChatCapability):
    """``fallback_on`` の例外で失敗したら次のプロバイダへ切り替える (ADR-07/08)。"""

    provider_name: ClassVar[str] = "fallback"

    def __init__(
        self,
        providers: Sequence[ChatCapability],
        *,
        fallback_on: tuple[type[OmniError], ...] = DEFAULT_FALLBACK_ON,
        model_map: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("At least one provider is required.")
        self.providers = list(providers)
        self.fallback_on = fallback_on
        self.model_map = {k: dict(v) for k, v in (model_map or {}).items()}
        self.supported_builtins = frozenset().union(*(p.supported_builtins for p in self.providers))

    def _map_model(self, provider: ChatCapability, model: str | None) -> str | None:
        """別プロバイダに同じモデル名を渡さない。対応が無ければそのアダプターの default_model を使う。"""
        if model is None:
            return None
        mapped = self.model_map.get(provider.provider_name, {}).get(model)
        if mapped:
            return mapped
        return model if provider is self.providers[0] else None

    def _candidates(self, tools: Sequence[ToolSpec] | None) -> list[ChatCapability]:
        need = _requested_builtins(tools)
        usable = [p for p in self.providers if need <= p.supported_builtins]
        if not usable:
            raise UnsupportedFeatureError(
                f"no provider in the fallback chain supports {sorted(b.value for b in need)}",
                provider=self.provider_name,
            )
        return usable

    def _exhausted(self, errors: list[OmniError]) -> OmniError:
        last = errors[-1]
        for e in errors[:-1]:
            last.add_note(f"fallback: {e.provider} failed with {type(e).__name__}: {e.message}")
        return last

    def generate(
        self, messages: Sequence[ChatMessage], *, model: str | None = None,
        tools: Sequence[ToolSpec] | None = None, **kwargs: Any,
    ) -> ChatResponse:  # fmt: skip
        errors: list[OmniError] = []
        for p in self._candidates(tools):
            try:
                res = p.generate(messages, model=self._map_model(p, model), tools=tools, **kwargs)
            except self.fallback_on as e:
                errors.append(e)
                continue
            res.meta.fallback_from = [e.provider for e in errors]
            return res
        raise self._exhausted(errors)

    async def agenerate(
        self, messages: Sequence[ChatMessage], *, model: str | None = None,
        tools: Sequence[ToolSpec] | None = None, **kwargs: Any,
    ) -> ChatResponse:  # fmt: skip
        errors: list[OmniError] = []
        for p in self._candidates(tools):
            try:
                res = await p.agenerate(messages, model=self._map_model(p, model), tools=tools, **kwargs)
            except self.fallback_on as e:
                errors.append(e)
                continue
            res.meta.fallback_from = [e.provider for e in errors]
            return res
        raise self._exhausted(errors)

    def stream(
        self, messages: Sequence[ChatMessage], *, model: str | None = None,
        tools: Sequence[ToolSpec] | None = None, **kwargs: Any,
    ) -> Iterator[StreamEvent]:  # fmt: skip
        errors: list[OmniError] = []
        for p in self._candidates(tools):
            it = iter(p.stream(messages, model=self._map_model(p, model), tools=tools, **kwargs))
            try:
                first = next(it)
            except StopIteration:
                return
            except self.fallback_on as e:  # 最初のイベントの前だけ切り替える (ADR-07)
                errors.append(e)
                continue
            self._stamp(first, errors)
            yield first
            for ev in it:
                self._stamp(ev, errors)
                yield ev
            return
        raise self._exhausted(errors)

    async def astream(
        self, messages: Sequence[ChatMessage], *, model: str | None = None,
        tools: Sequence[ToolSpec] | None = None, **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:  # fmt: skip
        errors: list[OmniError] = []
        for p in self._candidates(tools):
            gen = p.astream(messages, model=self._map_model(p, model), tools=tools, **kwargs)
            async with aclosing(gen) as it:  # type: ignore[type-var]
                try:
                    first = await anext(it)
                except StopAsyncIteration:
                    return
                except self.fallback_on as e:
                    errors.append(e)
                    continue
                self._stamp(first, errors)
                yield first
                async for ev in it:
                    self._stamp(ev, errors)
                    yield ev
                return
        raise self._exhausted(errors)

    @staticmethod
    def _stamp(ev: StreamEvent, errors: list[OmniError]) -> None:
        if ev.type == "message_complete" and ev.response is not None:
            ev.response.meta.fallback_from = [e.provider for e in errors]

    def close(self) -> None:
        for p in self.providers:
            p.close()

    async def aclose(self) -> None:
        for p in self.providers:
            await p.aclose()


# ============================================================================ OmniClient


class OmniClient:
    """全 10 領域のアダプターとテレメトリ・ライフサイクルを統合管理する。"""

    def __init__(
        self,
        chat: ChatCapability | None = None,
        judge: JudgeCapability | None = None,
        image: ImageCapability | None = None,
        audio: AudioCapability | None = None,
        embedding: EmbeddingCapability | None = None,
        rerank: RerankCapability | None = None,
        search: SearchCapability | None = None,
        doc_parser: DocumentParserCapability | None = None,
        sandbox: SandboxCapability | None = None,
        mcp: MCPCapability | None = None,
        on_response: Callable[[ResponseMeta], None] | None = None,
        pricing: PricingTable | None = None,
    ) -> None:
        self._caps: dict[str, Capability | None] = {
            "chat": chat, "judge": judge, "image": image, "audio": audio, "embedding": embedding,
            "rerank": rerank, "search": search, "doc_parser": doc_parser, "sandbox": sandbox, "mcp": mcp,
        }  # fmt: skip
        self.telemetry = TelemetryTracker()
        self.pricing = pricing or PricingTable()
        self._recorder = _Recorder(self.telemetry, self.pricing, on_response)
        self._chat_proxy = InstrumentedChat(chat, self._recorder) if chat is not None else None
        self._embedding_proxy = (
            InstrumentedEmbedding(embedding, self._recorder) if embedding is not None else None
        )

    def _get(self, name: str) -> Any:
        cap = self._caps[name]
        if cap is None:
            raise CapabilityNotConfiguredError(f"{name} provider is not configured.", provider="omni")
        return cap

    @property
    def chat(self) -> ChatCapability:
        if self._chat_proxy is None:
            raise CapabilityNotConfiguredError("chat provider is not configured.", provider="omni")
        return self._chat_proxy

    @property
    def tools(self) -> ToolRunner:
        from .tools import ToolRunner

        return ToolRunner(self.chat, search=self._caps["search"], sandbox=self._caps["sandbox"])  # type: ignore[arg-type]

    @property
    def judge(self) -> JudgeCapability:
        cap: JudgeCapability = self._get("judge")
        return cap

    @property
    def image(self) -> ImageCapability:
        cap: ImageCapability = self._get("image")
        return cap

    @property
    def audio(self) -> AudioCapability:
        cap: AudioCapability = self._get("audio")
        return cap

    @property
    def embedding(self) -> EmbeddingCapability:
        if self._embedding_proxy is None:
            raise CapabilityNotConfiguredError("embedding provider is not configured.", provider="omni")
        return self._embedding_proxy

    @property
    def rerank(self) -> RerankCapability:
        cap: RerankCapability = self._get("rerank")
        return cap

    @property
    def search(self) -> SearchCapability:
        cap: SearchCapability = self._get("search")
        return cap

    @property
    def doc_parser(self) -> DocumentParserCapability:
        cap: DocumentParserCapability = self._get("doc_parser")
        return cap

    @property
    def sandbox(self) -> SandboxCapability:
        cap: SandboxCapability = self._get("sandbox")
        return cap

    @property
    def mcp(self) -> MCPCapability:
        cap: MCPCapability = self._get("mcp")
        return cap

    # ------------------------------------------------------------------ lifecycle

    def _configured(self) -> list[Capability]:
        seen: set[int] = set()
        out: list[Capability] = []
        for cap in self._caps.values():
            if cap is not None and id(cap) not in seen:
                seen.add(id(cap))
                out.append(cap)
        return out

    def close(self) -> None:
        errors: list[Exception] = []
        for cap in self._configured():
            try:
                cap.close()
            except Exception as e:
                errors.append(e)
        if errors:
            raise ExceptionGroup("errors while closing OmniClient", errors)

    async def aclose(self) -> None:
        """1 つが失敗しても残りは閉じる。失敗はまとめて ExceptionGroup で送出する。"""
        errors: list[Exception] = []
        for cap in self._configured():
            try:
                await cap.aclose()
            except Exception as e:
                errors.append(e)
        if errors:
            raise ExceptionGroup("errors while closing OmniClient", errors)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()
