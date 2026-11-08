"""FasterWhisperAudioAdapter — faster-whisper（CTranslate2・ローカル）による音声認識（extras ``[audio]``）。

- 既定は medium・CPU・int8。モデルは初回に OS 標準のキャッシュ配下へダウンロードする（medium 約 1.5GB）
- 入力は Path / bytes / ストリーム（デコードは faster-whisper 同梱の PyAV。ffmpeg の別途導入は不要）
"""

from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import anyio.to_thread

from ..._media import rewind, tell_or_none
from ...exceptions import InvalidRequestError, OmniError, UnsupportedFeatureError
from ...types import AudioTranscriptionResponse, FileRef, ResponseMeta, TranscriptSegment, UrlRef
from .._audio_base import TranscriptionOnlyAudio
from ..fastembed._lazy import default_cache_dir

__all__ = ["DEFAULT_WHISPER_MODEL", "FasterWhisperAudioAdapter"]

logger = logging.getLogger("omni_ai")

DEFAULT_WHISPER_MODEL = "medium"


def _whisper_factory(**kwargs: Any) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - extras 未導入
        raise ImportError(
            'FasterWhisperAudioAdapter requires faster-whisper: pip install "omni-ai-sdk[audio]"'
        ) from exc
    return WhisperModel(**kwargs)


class FasterWhisperAudioAdapter(TranscriptionOnlyAudio):
    provider_name: ClassVar[str] = "faster-whisper"

    def __init__(
        self,
        *,
        model: str = DEFAULT_WHISPER_MODEL,
        device: str = "cpu",
        compute_type: str = "int8",
        cpu_threads: int = 0,
        cache_dir: Path | str | None = None,
        local_files_only: bool = False,
        beam_size: int = 5,
        vad_filter: bool = True,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.local_files_only = local_files_only
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self._factory = model_factory or _whisper_factory
        self._model: Any = None
        self._lock = threading.Lock()
        self._closed = False

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> Any:
        if self._closed:
            raise OmniError("adapter is closed", provider=self.provider_name)
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    self._model = self._factory(
                        model_size_or_path=self.model_name,
                        device=self.device,
                        compute_type=self.compute_type,
                        cpu_threads=self.cpu_threads,
                        download_root=str(self.cache_dir),
                        local_files_only=self.local_files_only,
                    )
        return self._model

    def transcribe(
        self,
        audio: Any,
        *,
        model: str | None = None,
        language: str | None = None,
        **provider_options: Any,
    ) -> AudioTranscriptionResponse:
        if model is not None and model != self.model_name:
            raise UnsupportedFeatureError(
                f"adapter is bound to {self.model_name!r}; create another one for {model!r}",
                provider=self.provider_name,
                model=model,
            )
        if isinstance(audio, (UrlRef, FileRef)):
            raise UnsupportedFeatureError(
                "faster-whisper transcribes local audio only (Path / bytes / stream)",
                provider=self.provider_name,
            )
        if isinstance(audio, str):
            raise InvalidRequestError(
                "pass pathlib.Path(...) for files (str is ambiguous)", provider=self.provider_name
            )
        opts = provider_options.get(self.provider_name, {})
        start = None
        if isinstance(audio, bytes):
            source: Any = io.BytesIO(audio)
        elif isinstance(audio, Path):
            source = str(audio)
        else:
            source = audio
            start = tell_or_none(audio)
        try:
            segments, info = self.load().transcribe(
                source,
                language=language or None,
                beam_size=opts.get("beam_size", self.beam_size),
                vad_filter=opts.get("vad_filter", self.vad_filter),
            )
            segs = [TranscriptSegment(start=float(s.start), end=float(s.end), text=s.text) for s in segments]
        except OmniError:
            raise
        except Exception as exc:
            raise InvalidRequestError(
                f"faster-whisper failed: {type(exc).__name__}: {exc}",
                provider=self.provider_name,
                raw_error=exc,
            ) from exc
        finally:
            if start is not None:
                rewind(audio, start)
        return AudioTranscriptionResponse(
            text="".join(s.text for s in segs).strip(),
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            duration_seconds=getattr(info, "duration", None),
            segments=segs,
            meta=ResponseMeta(provider=self.provider_name, model=self.model_name, estimated_cost_usd=0.0),
        )

    async def atranscribe(self, audio: Any, **kwargs: Any) -> AudioTranscriptionResponse:
        return await anyio.to_thread.run_sync(lambda: self.transcribe(audio, **kwargs))

    def close(self) -> None:
        self._model = None
        self._closed = True

    async def aclose(self) -> None:
        self.close()
