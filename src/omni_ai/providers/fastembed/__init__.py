"""FastEmbedEmbeddingAdapter — プロセス内のローカル埋め込み（fastembed / ONNX）。

- 既定モデル: ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``（384 次元、多言語）
- 出力: L2 正規化した float（sentence-transformers の ``normalize_embeddings=True`` 互換）

依存は extras（``pip install "omni-ai-sdk[local]"``）。コア依存には入れない（ADR-02 / ADR-03）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import anyio.to_thread

from ...capabilities import EmbeddingCapability, EmbeddingTask
from ...compat import PlatformCompat
from ...exceptions import OmniError, UnsupportedFeatureError
from ...types import EmbeddingResponse, ResponseMeta
from .._embed_base import empty_response, l2_normalize, prepare_texts

__all__ = ["DEFAULT_FASTEMBED_MODEL", "FastEmbedEmbeddingAdapter"]

logger = logging.getLogger("omni_ai")

DEFAULT_FASTEMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

ModelFactory = Callable[..., Any]


def _fastembed_factory(**kwargs: Any) -> Any:
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:  # pragma: no cover - extras 未導入
        raise ImportError(
            'FastEmbedEmbeddingAdapter requires fastembed: pip install "omni-ai-sdk[local]"'
        ) from exc
    return TextEmbedding(**kwargs)


class FastEmbedEmbeddingAdapter(EmbeddingCapability):
    provider_name: ClassVar[str] = "fastembed"

    def __init__(
        self,
        *,
        model: str = DEFAULT_FASTEMBED_MODEL,
        cache_dir: Path | str | None = None,
        threads: int | None = None,
        local_files_only: bool = False,
        batch_size: int = 64,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self.default_model: str = model
        # 既定は OS 標準のキャッシュ（platformdirs）。他のアプリとモデルを共有するなら、そのディレクトリを渡す
        self.cache_dir = (
            Path(cache_dir) if cache_dir else PlatformCompat.get_cache_dir(create=False) / "models"
        )
        self.threads = threads
        self.local_files_only = local_files_only
        self.batch_size = batch_size
        self._factory = model_factory or _fastembed_factory
        self._model: Any = None
        self._lock = threading.Lock()
        self._closed = False

    @property
    def model_name(self) -> str:
        return self.default_model or DEFAULT_FASTEMBED_MODEL

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """モデルを読み込む（初回の embed でも自動で呼ばれる）。スレッド安全。"""
        if self._closed:
            raise OmniError("adapter is closed", provider=self.provider_name)
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            logger.info("loading local embedding model %s (cache=%s)", self.default_model, self.cache_dir)
            self._model = self._factory(
                model_name=self.default_model,
                cache_dir=str(self.cache_dir),
                threads=self.threads,
                local_files_only=self.local_files_only,
            )
            logger.info("local embedding model loaded in %.1fs", time.perf_counter() - started)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        self.load()
        return [[float(x) for x in v] for v in self._model.embed(texts, batch_size=self.batch_size)]

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
        if model is not None and model != self.default_model:
            raise UnsupportedFeatureError(
                f"adapter is bound to {self.default_model!r}; create another one for {model!r}",
                provider=self.provider_name,
                model=model,
            )
        items = prepare_texts(texts, self.provider_name)
        if not items:
            return empty_response(self.provider_name, self.model_name)
        if task is not None:
            logger.debug("local embedding: task=%s ignored", task)
        vectors = self._encode(items)
        if dimensions is not None and vectors and dimensions != len(vectors[0]):
            raise UnsupportedFeatureError(
                f"{self.default_model} has fixed {len(vectors[0])} dimensions (requested {dimensions})",
                provider=self.provider_name,
                model=self.default_model,
            )
        if normalize:
            vectors = l2_normalize(vectors)
        return EmbeddingResponse(
            vectors=vectors,
            dimensions=len(vectors[0]),
            normalized=normalize,
            meta=ResponseMeta(provider=self.provider_name, model=self.default_model, estimated_cost_usd=0.0),
        )

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
        # ONNX の推論は CPU を使うので、イベントループを止めないようスレッドで実行する
        return await anyio.to_thread.run_sync(
            lambda: self.embed(texts, model=model, dimensions=dimensions, task=task, normalize=normalize)
        )

    def close(self) -> None:
        self._model = None
        self._closed = True

    async def aclose(self) -> None:
        self.close()
