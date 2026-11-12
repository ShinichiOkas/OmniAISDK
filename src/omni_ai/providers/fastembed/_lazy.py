"""fastembed のモデルを初回利用時に 1 度だけ読み込む共通部品（スレッド安全）。

トークナイザの共有（1.16）: ``tokenizer_donor`` に 1 本目の ``LazyModel`` を渡すと、2 本目は
fastembed 内部の ``model.tokenizer`` / ``special_token_to_id`` を使い回し、``Tokenizer.from_file``
が GIL を ~1 秒握る問題を避ける（下流プロジェクトの ``_donor_tokenizer`` と同じ発想）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...compat import PlatformCompat
from ...exceptions import OmniError

__all__ = ["LazyModel", "default_cache_dir"]

logger = logging.getLogger("omni_ai")


def default_cache_dir() -> Path:
    """既定は OS 標準のキャッシュ（platformdirs）配下の models。"""
    return PlatformCompat.get_cache_dir(create=False) / "models"


def _donor_internals(donor: LazyModel | None) -> tuple[Any, dict[str, int], str] | None:
    """読み込み済みの donor から ``(tokenizer, special_token_to_id, model_dir)`` を取り出す。"""
    if donor is None or donor._model is None:
        return None
    inner = getattr(donor._model, "model", None)
    tok = getattr(inner, "tokenizer", None)
    special = getattr(inner, "special_token_to_id", None)
    model_dir = getattr(inner, "_model_dir", None)
    if tok is None or not isinstance(special, dict) or model_dir is None:
        logger.info(
            "fastembed donor: cannot reuse the shared model's tokenizer (fastembed internals changed?)"
        )
        return None
    return tok, special, str(model_dir)


def _open_reusing_tokenizer(donor: tuple[Any, dict[str, int], str], **kw: Any) -> Any:
    """fastembed 0.8 の内部: ORT のセッションだけ開き、トークナイザは donor のものを差す。

    版替えで ``_load_onnx_model`` の形が変わっていたら ``TypeError`` を上げて
    呼び手が従来どおりの読み込みへ縮退する。
    """
    from fastembed import TextEmbedding
    from fastembed.common.onnx_model import OnnxModel
    from fastembed.text.onnx_embedding import OnnxTextEmbedding
    from fastembed.text.onnx_text_model import OnnxTextModel

    tok, special, donor_dir = donor
    te = TextEmbedding(lazy_load=True, **kw)
    inner = te.model
    cls = type(inner)
    if cls._load_onnx_model is not OnnxTextModel._load_onnx_model or (
        cls.load_onnx_model is not OnnxTextEmbedding.load_onnx_model
    ):
        raise TypeError(f"{cls.__name__} overrides the fastembed loader — not reusing the tokenizer")
    if Path(inner._model_dir).resolve() != Path(donor_dir).resolve():
        raise ValueError(f"model dir differs from donor: {inner._model_dir} != {donor_dir}")
    OnnxModel._load_onnx_model(
        inner,
        model_dir=inner._model_dir,
        model_file=inner.model_description.model_file,
        threads=inner.threads,
        providers=inner.providers,
        cuda=inner.cuda,
        device_id=inner.device_id,
        extra_session_options=inner._extra_session_options,
    )
    if getattr(inner, "model", None) is None:
        raise RuntimeError("the ONNX session was not opened")
    inner.tokenizer = tok
    inner.special_token_to_id = dict(special)
    return te


class LazyModel:
    def __init__(
        self,
        *,
        model_name: str,
        factory: Callable[..., Any],
        cache_dir: Path,
        threads: int | None,
        local_files_only: bool,
        tokenizer_donor: LazyModel | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.threads = threads
        self.local_files_only = local_files_only
        self._factory = factory
        self._tokenizer_donor = tokenizer_donor
        self.tokenizer_reused = False  # 読み込みに成功したあとに立つ
        self._model: Any = None
        self._lock = threading.Lock()
        self.closed = False

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def get(self) -> Any:
        if self.closed:
            raise OmniError("adapter is closed", provider="fastembed")
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                started = time.perf_counter()
                logger.info("loading fastembed model %s (cache=%s)", self.model_name, self.cache_dir)
                self._model = self._open()
                logger.info(
                    "fastembed model loaded in %.1fs (tokenizer_reused=%s)",
                    time.perf_counter() - started,
                    self.tokenizer_reused,
                )
        return self._model

    def _open(self) -> Any:
        kw: dict[str, Any] = {
            "model_name": self.model_name,
            "cache_dir": str(self.cache_dir),
            "threads": self.threads,
            "local_files_only": self.local_files_only,
        }
        donor = _donor_internals(self._tokenizer_donor)
        if donor is None:
            return self._factory(**kw)
        # donor があるときは fastembed の内部を直接触る実装でしか意味がない。
        # ユーザー差し替えの factory（試験の偽物など）では縮退して従来どおり呼ぶ。
        if self._factory.__module__ != "omni_ai.providers.fastembed":
            return self._factory(**kw)
        try:
            te = _open_reusing_tokenizer(donor, **kw)
            self.tokenizer_reused = True
            return te
        except Exception:
            logger.warning(
                "fastembed: reusing the donor tokenizer failed — loading an own copy (holds the GIL ~1s)",
                exc_info=True,
            )
            self.tokenizer_reused = False
            return self._factory(**kw)

    def close(self) -> None:
        self._model = None
        self.closed = True
