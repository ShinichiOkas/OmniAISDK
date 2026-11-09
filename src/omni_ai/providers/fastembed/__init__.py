"""FastEmbedEmbeddingAdapter — プロセス内のローカル埋め込み（fastembed / ONNX）。

- 既定モデル: ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``（384 次元、多言語）
- 出力: L2 正規化した float32（sentence-transformers の ``normalize_embeddings=True`` 互換、
  保存済みベクトルと bit 単位で一致する順序で正規化）
- numpy で返す道: ``embed_array()`` / ``aembed_array()``（float32 の ``np.ndarray``）
- トークナイザの共有: ``tokenizer_donor=`` で 2 本目のモデルを開くときに
  1 本目のトークナイザを使い回す（``Tokenizer.from_file`` が GIL を ~1 秒握る問題を避ける）

依存は extras（``pip install "omni-ai-sdk[local]"``）。コア依存には入れない（ADR-02 / ADR-03）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import anyio.to_thread

from ...capabilities import EmbeddingCapability, EmbeddingTask
from ...exceptions import UnsupportedFeatureError
from ...types import EmbeddingResponse, ResponseMeta
from .._embed_base import empty_response, prepare_texts
from ._lazy import LazyModel, default_cache_dir
from ._numpy_compat import normalize_bit_compat

if TYPE_CHECKING:  # pragma: no cover - 型のみ
    import numpy as np

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
        tokenizer_donor: "FastEmbedEmbeddingAdapter | None" = None,
        bit_compat_normalize: bool = True,
    ) -> None:
        self.default_model: str = model
        self.batch_size = batch_size
        # 保存済みベクトルと bit 互換の正規化を使うか（既定: True）。False なら pure-Python の L2 正規化
        self.bit_compat_normalize = bit_compat_normalize
        # 既定は OS 標準のキャッシュ（platformdirs）。他のアプリとモデルを共有するなら、そのディレクトリを渡す
        self._lazy = LazyModel(
            model_name=model,
            factory=model_factory or _fastembed_factory,
            cache_dir=Path(cache_dir) if cache_dir else default_cache_dir(),
            threads=threads,
            local_files_only=local_files_only,
            tokenizer_donor=tokenizer_donor._lazy if tokenizer_donor is not None else None,
        )

    @property
    def model_name(self) -> str:
        return self.default_model or DEFAULT_FASTEMBED_MODEL

    @property
    def cache_dir(self) -> Path:
        return self._lazy.cache_dir

    @property
    def is_loaded(self) -> bool:
        return self._lazy.loaded

    @property
    def _model(self) -> Any:  # テスト・後方互換用
        return self._lazy._model

    def load(self) -> None:
        """モデルを読み込む（初回の embed でも自動で呼ばれる）。スレッド安全。"""
        self._lazy.get()

    def _encode_array(self, texts: list[str]) -> "np.ndarray":
        """fastembed の反復子を float32 の 2 次元 ``np.ndarray`` に集める（0 コピーに近い道）。"""
        import numpy as np

        model = self._lazy.get()
        vecs = list(model.embed(texts, batch_size=self.batch_size))
        return np.asarray(vecs, dtype=np.float32)

    def _normalize_array(self, arr: "np.ndarray") -> "np.ndarray":
        if self.bit_compat_normalize:
            return normalize_bit_compat(arr)
        # pure-Python 流の L2（丸めが異なるので保存済みベクトルと bit 一致しない）
        import numpy as np

        a = np.asarray(arr, dtype=np.float32)
        norm = np.linalg.norm(a, axis=-1, keepdims=True)
        norm = np.where(norm == 0, 1.0, norm)
        return a / norm  # float32 に再度落とさない

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
        arr = self._encode_array(items)
        if dimensions is not None and arr.size and dimensions != arr.shape[-1]:
            raise UnsupportedFeatureError(
                f"{self.default_model} has fixed {arr.shape[-1]} dimensions (requested {dimensions})",
                provider=self.provider_name,
                model=self.default_model,
            )
        if normalize:
            arr = self._normalize_array(arr)
        vectors = [[float(x) for x in row] for row in arr]
        return EmbeddingResponse(
            vectors=vectors,
            dimensions=int(arr.shape[-1]) if arr.size else 0,
            normalized=normalize,
            meta=ResponseMeta(provider=self.provider_name, model=self.default_model, estimated_cost_usd=0.0),
        )

    # ---------- numpy で返す道（1.15） ----------

    def embed_array(
        self,
        texts: str | Sequence[str],
        *,
        normalize: bool = True,
    ) -> "np.ndarray":
        """float32 の ``np.ndarray`` を返す（shape: ``(N, dim)``、空なら ``(0,)``）。

        下流の ``batch_encode`` 相当と同じ dtype・同じ正規化で、保存済みベクトルと bit 互換。
        ``normalize=False`` にすると fastembed の生出力（float64 を float32 に落としたもの）を返す。
        """
        import numpy as np

        items = prepare_texts(texts, self.provider_name)
        if not items:
            return np.zeros((0,), dtype=np.float32)
        arr = self._encode_array(items)
        if normalize:
            arr = self._normalize_array(arr)
        return arr

    async def aembed_array(
        self,
        texts: str | Sequence[str],
        *,
        normalize: bool = True,
    ) -> "np.ndarray":
        return await anyio.to_thread.run_sync(lambda: self.embed_array(texts, normalize=normalize))

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
        self._lazy.close()

    async def aclose(self) -> None:
        self.close()
