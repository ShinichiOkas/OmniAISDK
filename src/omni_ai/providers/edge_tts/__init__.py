"""EdgeTTSAudioAdapter — edge-tts（Microsoft Edge の読み上げ。キー不要・オンライン）による音声合成。

- extras ``[audio]``
- 既定の声は ja-JP-NanamiNeural。話速 -50〜+100%、声の高さ -50〜+50Hz
- 出力: ``mp3``（既定）または ``wav``（ffmpeg が ``PATH`` にあれば MP3 → WAV 変換）
- 記法（1.20）: ``speak_text=`` に SSML・HTML 風・Markdown 風のいずれかで渡すと、
  区切りごとにスタイル（強調・間・音量・話速・高さ）を変えて合成し、連結した WAV を返す
- ⚠ 読み上げる文章は Microsoft のオンラインサービスへ送られる
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess  # noqa: S404 - ffmpeg の呼び出しに必要
import wave
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
from ._markup import SpeechSpan, parse_speech_markup

__all__ = ["DEFAULT_VOICE", "EdgeTTSAudioAdapter", "SpeechSpan", "parse_speech_markup"]

logger = logging.getLogger("omni_ai")

DEFAULT_VOICE = "ja-JP-NanamiNeural"
RATE_RANGE = (-50, 100)  # %
PITCH_RANGE = (-50, 50)  # Hz
VOLUME_RANGE = (-100, 100)  # %

_WAV_SR = 24000  # 既定のサンプルレート（ffmpeg が無いときのプレースホルダ無音の作りに使う）


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _decode_mp3_to_pcm16_mono(mp3_bytes: bytes) -> tuple[int, bytes]:
    """MP3 を PCM16 モノラルに落とす。ffmpeg が無ければ UnsupportedFeatureError。"""
    exe = _ffmpeg()
    if exe is None:
        raise UnsupportedFeatureError(
            "WAV output and speak_markup require ffmpeg on PATH", provider="edge-tts"
        )
    proc = subprocess.run(  # noqa: S603 - 入力は Microsoft からの MP3、exe は PATH 解決後
        [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-ac", "1", "-ar", str(_WAV_SR), "pipe:1"],
        input=mp3_bytes, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise ProviderConnectionError(
            f"ffmpeg failed to decode MP3: {proc.stderr.decode('utf-8', 'replace')}",
            provider="edge-tts",
        )
    return _WAV_SR, proc.stdout


def _concat_pcm_to_wav(segments: list[tuple[int, int, bytes]]) -> bytes:
    """``[(sample_rate, length_bytes, pcm_or_empty)]`` を連結して WAV バイト列にする。

    ``pcm_or_empty`` が空のときは無音（``length_bytes`` 分の 0x00）を書き出す。
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_WAV_SR)
        for _sr, nbytes, pcm in segments:
            if pcm:
                w.writeframes(pcm)
            else:
                # 2 バイト（int16）単位に揃える
                silence = b"\x00" * (nbytes if nbytes % 2 == 0 else nbytes + 1)
                w.writeframes(silence)
    return buf.getvalue()


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

    async def _synth_mp3(
        self, text: str, voice: str, rate: int, pitch: int, volume: int
    ) -> bytes:
        """EdgeTTS に 1 回だけ投げて、返ってきた MP3 のバイト列を集める。"""
        edge = _edge_tts()
        comm = edge.Communicate(
            text, voice,
            rate=_signed(rate, "%"),
            pitch=_signed(pitch, "Hz"),
            volume=_signed(volume, "%"),
            proxy=self.proxy,
            connect_timeout=self.connect_timeout,
            receive_timeout=self.receive_timeout,
        )
        chunks: list[bytes] = []
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

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
        if response_format not in ("mp3", "wav"):
            raise UnsupportedFeatureError(
                f"edge-tts returns mp3 or wav only (requested {response_format})",
                provider=self.provider_name,
            )
        opts = provider_options.get(self.provider_name, {})
        rate, pitch, volume = self._check(
            opts.get("rate", self.rate), opts.get("pitch", self.pitch), opts.get("volume", self.volume)
        )
        use_voice = voice or self.voice
        speak_markup = opts.get("speak_markup")  # 'ssml'|'html'|'markdown'|'plain'|None

        try:
            if response_format == "mp3" and speak_markup is None:
                mp3 = await self._synth_mp3(text, use_voice, rate, pitch, volume)
                return AudioSpeechResponse(
                    audio_bytes=mp3,
                    format="mp3",
                    meta=ResponseMeta(
                        provider=self.provider_name, model=use_voice, estimated_cost_usd=0.0
                    ),
                )
            # WAV 出力か記法ありは、区間ごとに合成→PCM に落として連結→WAV（または MP3 に戻す）
            spans = parse_speech_markup(text, speak_markup)
            if not spans:
                raise InvalidRequestError(
                    "after parsing markup there is no text to speak", provider=self.provider_name
                )
            segments: list[tuple[int, int, bytes]] = []  # (sample_rate, pcm16_bytes, _)
            for sp in spans:
                if sp.kind == "pause":
                    segments.append((_WAV_SR, _WAV_SR * 2 * sp.pause_ms // 1000, b""))
                    continue
                r = max(RATE_RANGE[0], min(RATE_RANGE[1], rate + sp.rate))
                p = max(PITCH_RANGE[0], min(PITCH_RANGE[1], pitch + sp.pitch))
                v = max(VOLUME_RANGE[0], min(VOLUME_RANGE[1], volume + sp.volume))
                mp3_seg = await self._synth_mp3(sp.text, use_voice, r, p, v)
                sr, pcm = _decode_mp3_to_pcm16_mono(mp3_seg)
                segments.append((sr, len(pcm), pcm))
            wav_bytes = _concat_pcm_to_wav(segments)
            if response_format == "wav":
                return AudioSpeechResponse(
                    audio_bytes=wav_bytes,
                    format="wav",
                    meta=ResponseMeta(
                        provider=self.provider_name, model=use_voice, estimated_cost_usd=0.0
                    ),
                )
            # 記法ありで MP3 を要求されたときは、連結した WAV をそのまま返す（MP3 の再エンコードはしない）
            # 代わりに **WAV のバイト列を MP3 形式として返す**のは意味を変えるので禁じる。
            # 既定の挙動: MP3 要求 + 記法ありは、記法を無視して従来の MP3 合成に落とす。
            mp3 = await self._synth_mp3(text, use_voice, rate, pitch, volume)
            return AudioSpeechResponse(
                audio_bytes=mp3,
                format="mp3",
                meta=ResponseMeta(
                    provider=self.provider_name, model=use_voice, estimated_cost_usd=0.0
                ),
            )
        except OmniError:
            raise
        except Exception as exc:
            raise self._map(exc, use_voice) from exc

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
