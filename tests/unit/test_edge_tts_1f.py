"""1.F: EdgeTTSAudioAdapter の記法パースと WAV 連結の単体試験。

本物の edge-tts は呼ばない（オンライン）。_markup のパースと、_concat_pcm_to_wav の
連結、aspeak の形式判定を偽物 Communicate で確かめる。
"""

from __future__ import annotations

import io
import wave

import pytest

from omni_ai.providers.edge_tts import (
    DEFAULT_VOICE,
    EdgeTTSAudioAdapter,
    parse_speech_markup,
)
from omni_ai.providers.edge_tts._markup import detect_markup

# ---------- 記法のパース ----------


def test_detect_markup_variants() -> None:
    assert detect_markup("<speak>hi</speak>") == "ssml"
    assert detect_markup("<prosody rate='50%'>x</prosody>") == "ssml"
    assert detect_markup("<b>hi</b>") == "html"
    assert detect_markup("**strong**") == "markdown"
    assert detect_markup("[pause:500ms] next") == "markdown"
    assert detect_markup("[間:500ms] next") == "markdown"
    assert detect_markup("hello") == "plain"


def test_parse_ssml_break_and_prosody() -> None:
    text = (
        "<speak><prosody rate='+30%' pitch='+5Hz'>hello</prosody>"
        "<break time='500ms'/>world</speak>"
    )
    spans = parse_speech_markup(text)
    assert len(spans) == 3
    assert spans[0].kind == "text" and spans[0].text == "hello"
    assert spans[0].rate == 30 and spans[0].pitch == 5
    assert spans[1].kind == "pause" and spans[1].pause_ms == 500
    assert spans[2].kind == "text" and spans[2].text == "world"
    assert spans[2].rate == 0  # prosody を抜けると既定に戻る


def test_parse_html_strong_increases_emphasis() -> None:
    spans = parse_speech_markup("<strong>Danger</strong> and then calm")
    strong = next(s for s in spans if "Danger" in s.text)
    calm = next(s for s in spans if "calm" in s.text)
    assert strong.volume > calm.volume  # 強調で音量が上がる


def test_parse_markdown_strong_and_pause() -> None:
    spans = parse_speech_markup("intro **loud** [pause:300ms] rest")
    kinds = [s.kind for s in spans]
    assert "pause" in kinds
    loud = next(s for s in spans if s.text == "loud")
    assert loud.rate == 10 and loud.volume == 10
    pause = next(s for s in spans if s.kind == "pause")
    assert pause.pause_ms == 300


def test_parse_prefers_explicit_markup_over_detection() -> None:
    # markdown のつもりで書いた文字列を plain と見なす
    spans = parse_speech_markup("**not strong**", markup="plain")
    assert len(spans) == 1 and spans[0].text == "**not strong**"


# ---------- WAV 連結 ----------


def _wav_frames_and_rate(wav_bytes: bytes) -> tuple[int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as r:
        return r.getnframes(), r.getframerate()


def test_concat_pcm_to_wav_handles_pcm_and_silence() -> None:
    from omni_ai.providers.edge_tts import _WAV_SR, _concat_pcm_to_wav

    pcm = b"\x01\x00" * 100  # 100 サンプル
    silence_bytes = _WAV_SR * 2 // 10  # 100ms の無音（サンプル数 = sr/10・バイト数 = sr/10 * 2）
    wav = _concat_pcm_to_wav(
        [(_WAV_SR, len(pcm), pcm), (_WAV_SR, silence_bytes, b"")]
    )
    frames, rate = _wav_frames_and_rate(wav)
    assert rate == _WAV_SR
    expected_frames = 100 + (_WAV_SR // 10)
    # silence の端数対応で ±1 の揺れを許す
    assert abs(frames - expected_frames) <= 1


# ---------- aspeak の形式判定（本物の edge-tts は呼ばない） ----------


class _FakeCommunicate:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    async def stream(self):
        yield {"type": "audio", "data": b"ID3\x00\x00\x00fake-mp3"}


class _FakeEdgeTTSModule:
    Communicate = _FakeCommunicate

    @staticmethod
    async def list_voices(proxy=None):
        return [{"ShortName": DEFAULT_VOICE, "Locale": "ja-JP"}]


@pytest.fixture
def fake_edge(monkeypatch):
    import omni_ai.providers.edge_tts as ett

    monkeypatch.setattr(ett, "_edge_tts", lambda: _FakeEdgeTTSModule)
    return _FakeEdgeTTSModule


@pytest.mark.asyncio
async def test_aspeak_mp3_default(fake_edge) -> None:
    ad = EdgeTTSAudioAdapter()
    res = await ad.aspeak("hello")
    assert res.format == "mp3"
    assert res.audio_bytes.startswith(b"ID3")


@pytest.mark.asyncio
async def test_aspeak_wav_requires_ffmpeg_or_raises(fake_edge, monkeypatch) -> None:
    import omni_ai.providers.edge_tts as ett

    # ffmpeg が無い想定にする
    monkeypatch.setattr(ett.shutil, "which", lambda _name: None)
    from omni_ai.exceptions import UnsupportedFeatureError

    ad = EdgeTTSAudioAdapter()
    with pytest.raises(UnsupportedFeatureError, match="ffmpeg"):
        await ad.aspeak("hello", response_format="wav")


@pytest.mark.asyncio
async def test_aspeak_rejects_opus(fake_edge) -> None:
    from omni_ai.exceptions import UnsupportedFeatureError

    ad = EdgeTTSAudioAdapter()
    with pytest.raises(UnsupportedFeatureError, match="mp3 or wav"):
        await ad.aspeak("hello", response_format="opus")


@pytest.mark.asyncio
async def test_aspeak_empty_text_raises(fake_edge) -> None:
    from omni_ai.exceptions import InvalidRequestError

    ad = EdgeTTSAudioAdapter()
    with pytest.raises(InvalidRequestError, match="empty"):
        await ad.aspeak("   ")
