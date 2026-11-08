"""音声の共通部分: 片方だけを担うアダプターの土台と、2 つを束ねる AudioRouter。"""

from __future__ import annotations

from typing import Any, ClassVar

from ..capabilities import AudioCapability
from ..exceptions import UnsupportedFeatureError
from ..types import AudioSpeechResponse, AudioTranscriptionResponse

__all__ = ["AudioRouter", "SpeechOnlyAudio", "TranscriptionOnlyAudio"]


class SpeechOnlyAudio(AudioCapability):
    """音声合成だけを担うアダプターの土台。transcribe は UnsupportedFeatureError。"""

    def transcribe(self, audio: Any, **kwargs: Any) -> AudioTranscriptionResponse:
        raise UnsupportedFeatureError(
            f"{self.provider_name} does not transcribe; use AudioRouter", provider=self.provider_name
        )

    async def atranscribe(self, audio: Any, **kwargs: Any) -> AudioTranscriptionResponse:
        return self.transcribe(audio, **kwargs)


class TranscriptionOnlyAudio(AudioCapability):
    """音声認識だけを担うアダプターの土台。speak は UnsupportedFeatureError。"""

    def speak(self, text: str, **kwargs: Any) -> AudioSpeechResponse:
        raise UnsupportedFeatureError(
            f"{self.provider_name} does not synthesize speech; use AudioRouter", provider=self.provider_name
        )

    async def aspeak(self, text: str, **kwargs: Any) -> AudioSpeechResponse:
        return self.speak(text, **kwargs)


class AudioRouter(AudioCapability):
    """合成と認識を別々のエンジンに任せる（例: 合成は edge-tts、認識は faster-whisper）。

    結果の ``meta.provider`` は、実際に処理したエンジンの名前になる。
    """

    provider_name: ClassVar[str] = "audio-router"

    def __init__(
        self, *, speech: AudioCapability | None = None, transcription: AudioCapability | None = None
    ) -> None:
        if speech is None and transcription is None:
            raise ValueError("specify speech= and/or transcription=")
        self.speech = speech
        self.transcription = transcription

    def _speaker(self) -> AudioCapability:
        if self.speech is None:
            raise UnsupportedFeatureError("no speech engine is configured", provider=self.provider_name)
        return self.speech

    def _transcriber(self) -> AudioCapability:
        if self.transcription is None:
            raise UnsupportedFeatureError(
                "no transcription engine is configured", provider=self.provider_name
            )
        return self.transcription

    def speak(self, text: str, **kwargs: Any) -> AudioSpeechResponse:
        return self._speaker().speak(text, **kwargs)

    async def aspeak(self, text: str, **kwargs: Any) -> AudioSpeechResponse:
        return await self._speaker().aspeak(text, **kwargs)

    def transcribe(self, audio: Any, **kwargs: Any) -> AudioTranscriptionResponse:
        return self._transcriber().transcribe(audio, **kwargs)

    async def atranscribe(self, audio: Any, **kwargs: Any) -> AudioTranscriptionResponse:
        return await self._transcriber().atranscribe(audio, **kwargs)

    def close(self) -> None:
        for e in (self.speech, self.transcription):
            if e is not None:
                e.close()

    async def aclose(self) -> None:
        for e in (self.speech, self.transcription):
            if e is not None:
                await e.aclose()
