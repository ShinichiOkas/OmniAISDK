"""埋め込みアダプターの共通部分（実装設計書 §14）。"""

from __future__ import annotations

import logging
import math
from abc import abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .._http import AsyncTransport, RetryPolicy, SyncTransport
from ..capabilities import EmbeddingCapability, EmbeddingTask
from ..exceptions import InvalidRequestError, OmniError
from ..types import EmbeddingResponse, ResponseMeta, Usage

__all__ = ["BaseHttpEmbeddingAdapter", "EmbedBatchResult", "EmbedRequest", "l2_normalize", "prepare_texts"]

logger = logging.getLogger("omni_ai")


def prepare_texts(texts: str | Sequence[str], provider: str) -> list[str]:
    items = [texts] if isinstance(texts, str) else list(texts)
    for i, t in enumerate(items):
        if not isinstance(t, str):
            raise InvalidRequestError(f"texts[{i}] is not a string", provider=provider)
        if not t.strip():
            # Gemini は 400、Ollama は空の配列を返して件数がずれる（実測）→ 送信前に揃えて弾く
            raise InvalidRequestError(f"texts[{i}] is empty", provider=provider)
    return items


def l2_normalize(vectors: list[list[float]]) -> list[list[float]]:
    out = []
    for v in vectors:
        norm = math.sqrt(sum(x * x for x in v))
        out.append([x / norm for x in v] if norm > 0 else list(v))
    return out


@dataclass
class EmbedRequest:
    texts: list[str]
    model: str
    dimensions: int | None = None
    task: EmbeddingTask | None = None
    timeout: float | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbedBatchResult:
    vectors: list[list[float]]
    prompt_tokens: int = 0
    model: str | None = None


def empty_response(provider: str, model: str) -> EmbeddingResponse:
    return EmbeddingResponse(vectors=[], dimensions=0, meta=ResponseMeta(provider=provider, model=model))


class BaseHttpEmbeddingAdapter(EmbeddingCapability):
    """HTTP で埋め込みを取るアダプターの骨格。バッチ分割・正規化・usage の合算をここで行う。"""

    max_batch_size: int = 100

    def __init__(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        default_model: str | None = None,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.default_model = default_model
        common: dict[str, Any] = {
            "provider": self.provider_name,
            "base_url": base_url,
            "headers": headers,
            "timeout": timeout,
            "retry": retry,
            "error_mapper": lambda s, b, h: self._map_error(s, b, h, None),
        }
        self._sync = SyncTransport(client=http_client, **common)
        self._async = AsyncTransport(client=async_http_client, **common)

    @abstractmethod
    def _endpoint(self, req: EmbedRequest, batch: list[str]) -> str: ...

    @abstractmethod
    def _build_body(self, req: EmbedRequest, batch: list[str]) -> dict[str, Any]: ...

    @abstractmethod
    def _parse_body(self, data: Any, req: EmbedRequest, batch: list[str]) -> EmbedBatchResult: ...

    @abstractmethod
    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError: ...

    def _preflight(self, req: EmbedRequest) -> None:
        """送信前の同期チェック。"""

    async def _apreflight(self, req: EmbedRequest) -> None:
        """送信前の非同期チェック。"""

    def _prepare(
        self,
        texts: str | Sequence[str],
        model: str | None,
        dimensions: int | None,
        task: EmbeddingTask | None,
        timeout: float | None,
        provider_options: dict[str, Any],
    ) -> EmbedRequest:
        resolved = model or self.default_model
        if not resolved:
            raise InvalidRequestError(
                f"model is required ({self.provider_name} embedding adapter has no default_model)",
                provider=self.provider_name,
            )
        ours: dict[str, Any] = {}
        for key, value in provider_options.items():
            if key == self.provider_name and isinstance(value, dict):
                ours = dict(value)
            else:
                logger.debug("%s embedding adapter ignored provider option %r", self.provider_name, key)
        return EmbedRequest(
            prepare_texts(texts, self.provider_name), resolved, dimensions, task, timeout, ours
        )

    def _batches(self, texts: list[str]) -> list[list[str]]:
        n = self.max_batch_size
        return [texts[i : i + n] for i in range(0, len(texts), n)]

    def _mapper(self, model: str) -> Any:
        def mapper(status: int, body: bytes, headers: Mapping[str, str]) -> OmniError:
            err = self._map_error(status, body, headers, model)
            err.model = err.model or model
            return err

        return mapper

    def _assemble(
        self, req: EmbedRequest, results: list[EmbedBatchResult], attempts: int, normalize: bool
    ) -> EmbeddingResponse:
        vectors = [v for r in results for v in r.vectors]
        if len(vectors) != len(req.texts):
            raise OmniError(
                f"provider returned {len(vectors)} vectors for {len(req.texts)} inputs",
                provider=self.provider_name,
                model=req.model,
            )
        if normalize:
            vectors = l2_normalize(vectors)
        tokens = sum(r.prompt_tokens for r in results)
        model = next((r.model for r in results if r.model), req.model)
        return EmbeddingResponse(
            vectors=vectors,
            dimensions=len(vectors[0]) if vectors else 0,
            normalized=normalize,
            meta=ResponseMeta(
                provider=self.provider_name,
                model=model,
                attempts=attempts,
                usage=Usage(prompt_tokens=tokens, total_tokens=tokens),
            ),
        )

    def embed(
        self,
        texts: str | Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
        task: EmbeddingTask | None = None,
        normalize: bool = True,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> EmbeddingResponse:
        req = self._prepare(texts, model, dimensions, task, timeout, provider_options)
        if not req.texts:
            return empty_response(self.provider_name, req.model)
        self._preflight(req)
        results: list[EmbedBatchResult] = []
        attempts = 1
        for batch in self._batches(req.texts):
            r = self._sync.request(
                "POST",
                self._endpoint(req, batch),
                json=self._build_body(req, batch),
                timeout=req.timeout,
                error_mapper=self._mapper(req.model),
            )
            attempts = max(attempts, r.attempts)
            results.append(self._parse_body(r.json(), req, batch))
        return self._assemble(req, results, attempts, normalize)

    async def aembed(
        self,
        texts: str | Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
        task: EmbeddingTask | None = None,
        normalize: bool = True,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> EmbeddingResponse:
        req = self._prepare(texts, model, dimensions, task, timeout, provider_options)
        if not req.texts:
            return empty_response(self.provider_name, req.model)
        await self._apreflight(req)
        results: list[EmbedBatchResult] = []
        attempts = 1
        for batch in self._batches(req.texts):
            r = await self._async.request(
                "POST",
                self._endpoint(req, batch),
                json=self._build_body(req, batch),
                timeout=req.timeout,
                error_mapper=self._mapper(req.model),
            )
            attempts = max(attempts, r.attempts)
            results.append(self._parse_body(r.json(), req, batch))
        return self._assemble(req, results, attempts, normalize)

    def close(self) -> None:
        self._sync.close()
        self._async.mark_closed_sync()

    async def aclose(self) -> None:
        await self._async.aclose()
        self._sync.close()
