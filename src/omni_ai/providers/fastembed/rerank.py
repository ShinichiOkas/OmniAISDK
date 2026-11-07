"""FastEmbedRerankAdapter — プロセス内のクロスエンコーダーによる再順位付け（fastembed / ONNX）。

- 既定モデル: ``BAAI/bge-reranker-v2-m3-int8``（多言語。師匠の決定 2026-11-08）
- スコア: モデルの出力はロジット（実測で関連 ≈ +7、無関係 ≈ -11）。既定でシグモイドを通して 0〜1 にする
  （クラウドの Rerank と揃えるため）。``fastembed={"raw_scores": True}`` で生の値
- 依存は extras（``pip install "omni-ai-sdk[local]"``）
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import anyio.to_thread

from ...capabilities import RerankCapability
from ...exceptions import InvalidRequestError, UnsupportedFeatureError
from ...types import RerankItem, RerankResponse, ResponseMeta
from ._lazy import LazyModel, default_cache_dir

__all__ = ["DEFAULT_RERANK_MODEL", "FastEmbedRerankAdapter"]

logger = logging.getLogger("omni_ai")

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-v2-m3-int8"


def _cross_encoder_factory(**kwargs: Any) -> Any:
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except ImportError as exc:  # pragma: no cover - extras 未導入
        raise ImportError(
            'FastEmbedRerankAdapter requires fastembed: pip install "omni-ai-sdk[local]"'
        ) from exc
    return TextCrossEncoder(**kwargs)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class FastEmbedRerankAdapter(RerankCapability):
    provider_name: ClassVar[str] = "fastembed"

    def __init__(
        self,
        *,
        model: str = DEFAULT_RERANK_MODEL,
        cache_dir: Path | str | None = None,
        threads: int | None = None,
        local_files_only: bool = False,
        batch_size: int = 64,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model
        self.batch_size = batch_size
        self._lazy = LazyModel(
            model_name=model,
            factory=model_factory or _cross_encoder_factory,
            cache_dir=Path(cache_dir) if cache_dir else default_cache_dir(),
            threads=threads,
            local_files_only=local_files_only,
        )

    @property
    def is_loaded(self) -> bool:
        return self._lazy.loaded

    def load(self) -> None:
        self._lazy.get()

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
        **provider_options: Any,
    ) -> RerankResponse:
        if model is not None and model != self.model_name:
            raise UnsupportedFeatureError(
                f"adapter is bound to {self.model_name!r}; create another one for {model!r}",
                provider=self.provider_name,
                model=model,
            )
        if not query or not query.strip():
            raise InvalidRequestError("query is empty", provider=self.provider_name)
        for i, d in enumerate(documents):
            if not isinstance(d, str) or not d.strip():
                raise InvalidRequestError(f"documents[{i}] is empty", provider=self.provider_name)
        if top_n is not None and top_n < 1:
            raise InvalidRequestError("top_n must be >= 1", provider=self.provider_name)
        opts: dict[str, Any] = {}
        for key, value in provider_options.items():
            if key == self.provider_name and isinstance(value, dict):
                opts = dict(value)
            else:
                logger.debug("fastembed rerank adapter ignored provider option %r", key)
        meta = ResponseMeta(provider=self.provider_name, model=self.model_name, estimated_cost_usd=0.0)
        if not documents:
            return RerankResponse(results=[], meta=meta)
        scores = [float(s) for s in self._lazy.get().rerank(query, documents, batch_size=self.batch_size)]
        if len(scores) != len(documents):
            raise InvalidRequestError(
                f"model returned {len(scores)} scores for {len(documents)} documents",
                provider=self.provider_name,
            )
        if not opts.get("raw_scores"):
            scores = [_sigmoid(s) for s in scores]
        order = sorted(range(len(documents)), key=lambda i: scores[i], reverse=True)
        if top_n is not None:
            order = order[:top_n]
        return RerankResponse(
            results=[RerankItem(index=i, document=documents[i], relevance_score=scores[i]) for i in order],
            meta=meta,
        )

    async def arerank(
        self,
        query: str,
        documents: list[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
        **provider_options: Any,
    ) -> RerankResponse:
        return await anyio.to_thread.run_sync(
            lambda: self.rerank(query, documents, model=model, top_n=top_n, **provider_options)
        )

    def close(self) -> None:
        self._lazy.close()

    async def aclose(self) -> None:
        self._lazy.close()
