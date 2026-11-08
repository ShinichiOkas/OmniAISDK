"""EdgeTTSAudioAdapter — edge-tts（Microsoft Edge の読み上げ。キー不要・オンライン）による音声合成。

- extras ``[audio]``
- 既定の声は ja-JP-NanamiNeural。話速 -50〜+100%、声の高さ -50〜+50Hz
- 出力は MP3 のみ（実測: edge-tts は MP3 を返す。他の形式は UnsupportedFeatureError）
- ⚠ 読み上げる文章は Microsoft のオンラインサービスへ送られる
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar, Literal

from ...exceptions import (
    InvalidRequestError,
    OmniError,
    ProviderConnectionError,
    ProviderTimeoutError,
    UnsupportedFeatureError,
)
from ...types import AudioSpeechResponse, ResponseMeta
from .._audio_base import SpeechOnlyAudio

__all__ = ["DEFAULT_VOICE", "EdgeTTSAudioAdapter"]

logger = logging.getLogger("omni_ai")

DEFAULT_VOICE = "ja-JP-NanamiNeural"
RATE_RANGE = (-50, 100)  # %
PITCH_RANGE = (-50, 50)  # Hz
VOLUME_RANGE = (-100, 100)  # %


def _edge_tts() -> Any:
    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover - extras 未導入
        raise ImportError('EdgeTTSAudioAdapter requires edge-tts: pip install "omni-ai-sdk[audio]"') from exc
    return edge_tts


def _signed(value: int, unit: str) -> str:
    return f"{value:+d}{unit}"


class EdgeTTSAudioAdapter(SpeechOnlyAudio):
    provider_name: ClassVar[str] = "edge-tts"

    def __init__(
        self,
        *,
        voice: str = DEFAULT_VOICE,
        rate: int = 0,
        pitch: int = 0,
        volume: int = 0,
        connect_timeout: int = 10,
        receive_timeout: int = 60,
        proxy: str | None = None,
    ) -> None:
        self.voice = voice
        self.rate, self.pitch, self.volume = self._check(rate, pitch, volume)
        self.connect_timeout = connect_timeout
        self.receive_timeout = receive_timeout
        self.proxy = proxy

    @staticmethod
    def _check(rate: Any, pitch: Any, volume: Any) -> tuple[int, int, int]:
        for name, val, (lo, hi), unit in (
            ("rate", rate, RATE_RANGE, "%"),
            ("pitch", pitch, PITCH_RANGE, "Hz"),
            ("volume", volume, VOLUME_RANGE, "%"),
        ):
            if isinstance(val, bool) or not isinstance(val, int) or not lo <= val <= hi:
                raise InvalidRequestError(
                    f"{name} must be an integer in {lo}..{hi}{unit} (got {val!r})", provider="edge-tts"
                )
        return int(rate), int(pitch), int(volume)

    async def aspeak(
        self,
        text: str,
        *,
        model: str | None = None,
        voice: str | None = None,
        response_format: Literal["mp3", "wav", "opus", "pcm"] = "mp3",
        **provider_options: Any,
    ) -> AudioSpeechResponse:
        if not text or not text.strip():
            raise InvalidRequestError("text is empty", provider=self.provider_name)
        if response_format != "mp3":
            raise UnsupportedFeatureError(
                f"edge-tts returns mp3 only (requested {response_format})", provider=self.provider_name
            )
        opts = provider_options.get(self.provider_name, {})
        rate, pitch, volume = self._check(
            opts.get("rate", self.rate), opts.get("pitch", self.pitch), opts.get("volume", self.volume)
        )
        use_voice = voice or self.voice
        edge = _edge_tts()
        comm = edge.Communicate(
            text,
            use_voice,
            rate=_signed(rate, "%"),
            pitch=_signed(pitch, "Hz"),
            volume=_signed(volume, "%"),
            proxy=self.proxy,
            connect_timeout=self.connect_timeout,
            receive_timeout=self.receive_timeout,
        )
        chunks: list[bytes] = []
        try:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    chunks.append(chunk["data"])
        except Exception as exc:
            raise self._map(exc, use_voice) from exc
        return AudioSpeechResponse(
            audio_bytes=b"".join(chunks),
            format="mp3",
            meta=ResponseMeta(provider=self.provider_name, model=use_voice, estimated_cost_usd=0.0),
        )

    def _map(self, exc: Exception, voice: str) -> OmniError:
        name = type(exc).__name__
        if name == "NoAudioReceived":  # 実測: 存在しない声でもこの例外になる
            return InvalidRequestError(
                f"edge-tts returned no audio (is voice {voice!r} valid? see alist_voices())",
                provider=self.provider_name,
                raw_error=exc,
            )
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return ProviderTimeoutError(
                f"edge-tts timed out: {exc}", provider=self.provider_name, raw_error=exc
            )
        return ProviderConnectionError(
            f"edge-tts failed: {name}: {exc}", provider=self.provider_name, raw_error=exc
        )

    def speak(
        self,
        text: str,
        *,
        model: str | None = None,
        voice: str | None = None,
        response_format: Literal["mp3", "wav", "opus", "pcm"] = "mp3",
        **provider_options: Any,
    ) -> AudioSpeechResponse:
        # edge-tts は非同期だけ。既にループが走る環境でも動くよう、別スレッドの新しいループで実行する
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                self.aspeak(
                    text, model=model, voice=voice, response_format=response_format, **provider_options
                ),
            ).result()

    async def alist_voices(self, locale: str | None = None) -> list[str]:
        try:
            voices = await _edge_tts().list_voices(proxy=self.proxy)
        except Exception as exc:
            raise self._map(exc, "-") from exc
        return sorted(
            v["ShortName"] for v in voices if locale is None or v.get("Locale", "").startswith(locale)
        )
