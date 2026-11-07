"""fastembed のモデルを初回利用時に 1 度だけ読み込む共通部品（スレッド安全）。"""

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


class LazyModel:
    def __init__(
        self,
        *,
        model_name: str,
        factory: Callable[..., Any],
        cache_dir: Path,
        threads: int | None,
        local_files_only: bool,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.threads = threads
        self.local_files_only = local_files_only
        self._factory = factory
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
                self._model = self._factory(
                    model_name=self.model_name,
                    cache_dir=str(self.cache_dir),
                    threads=self.threads,
                    local_files_only=self.local_files_only,
                )
                logger.info("fastembed model loaded in %.1fs", time.perf_counter() - started)
        return self._model

    def close(self) -> None:
        self._model = None
        self.closed = True
