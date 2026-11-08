"""音声の契約テスト。edge-tts / faster-whisper は偽物に差し替える（実物は live テスト）。"""

import io
import sys
import types
from pathlib import Path

import pytest

from omni_ai import InvalidRequestError, OmniClient, UnsupportedFeatureError
from omni_ai.providers import AudioRouter
from omni_ai.providers.edge_tts import DEFAULT_VOICE, EdgeTTSAudioAdapter
from omni_ai.providers.faster_whisper import DEFAULT_WHISPER_MODEL, FasterWhisperAudioAdapter

# ------------------------------------------------------------------ edge-tts（モジュールごと偽物）


class NoAudioReceived(Exception):
    pass


class FakeCommunicate:
    calls: list[dict] = []  # noqa: RUF012

    def __init__(self, text, voice, **kw) -> None:
        FakeCommunicate.calls.append({"text": text, "voice": voice, **kw})
        self.voice = voice

    async def stream(self):
        if self.voice.startswith("xx-"):
            raise NoAudioReceived("No audio was received.")
        yield {"type": "audio", "data": b"\xff\xf3"}
        yield {"type": "SentenceBoundary", "text": "x"}
        yield {"type": "audio", "data": b"\x00\x01"}


async def fake_list_voices(proxy=None):
    return [
        {"ShortName": "ja-JP-NanamiNeural", "Locale": "ja-JP"},
        {"ShortName": "en-US-EmmaNeural", "Locale": "en-US"},
    ]


@pytest.fixture
def fake_edge(monkeypatch: pytest.MonkeyPatch):
    mod = types.SimpleNamespace(Communicate=FakeCommunicate, list_voices=fake_list_voices)
    monkeypatch.setitem(sys.modules, "edge_tts", mod)
    FakeCommunicate.calls = []
    return FakeCommunicate


def test_speak_defaults_and_options(fake_edge) -> None:
    res = EdgeTTSAudioAdapter(rate=10).speak("こんにちは", **{"edge-tts": {"pitch": -5}})
    assert res.audio_bytes == b"\xff\xf3\x00\x01" and res.format == "mp3"
    assert res.meta.provider == "edge-tts" and res.meta.model == DEFAULT_VOICE == "ja-JP-NanamiNeural"
    call = fake_edge.calls[0]
    assert (call["rate"], call["pitch"], call["volume"]) == ("+10%", "-5Hz", "+0%")


async def test_aspeak_voice_override_and_list(fake_edge) -> None:
    a = EdgeTTSAudioAdapter()
    res = await a.aspeak("hello", voice="en-US-EmmaNeural")
    assert res.meta.model == "en-US-EmmaNeural"
    assert await a.alist_voices("ja") == ["ja-JP-NanamiNeural"]


def test_speak_errors(fake_edge) -> None:
    a = EdgeTTSAudioAdapter()
    with pytest.raises(InvalidRequestError, match="voice"):
        a.speak("x", voice="xx-XX-NoSuchNeural")
    with pytest.raises(InvalidRequestError):
        a.speak("  ")
    with pytest.raises(UnsupportedFeatureError):
        a.speak("x", response_format="wav")
    with pytest.raises(InvalidRequestError, match="rate"):
        EdgeTTSAudioAdapter(rate=200)
    with pytest.raises(UnsupportedFeatureError):
        a.transcribe(b"x")


# ------------------------------------------------------------------ faster-whisper（モデルを偽物に）


class Seg:
    def __init__(self, start, end, text) -> None:
        self.start, self.end, self.text = start, end, text


class FakeWhisper:
    instances = 0
    received: list = []  # noqa: RUF012

    def __init__(self, **kw) -> None:
        FakeWhisper.instances += 1
        self.kw = kw

    def transcribe(self, source, **kw):
        FakeWhisper.received.append((source, kw))
        if hasattr(source, "read"):
            source.read()
        info = types.SimpleNamespace(
            language=kw.get("language") or "ja", language_probability=0.99, duration=5.7
        )
        return iter([Seg(0.0, 2.0, "猫は1日に"), Seg(2.0, 5.0, "12時間眠る")]), info


def whisper(**kw) -> FasterWhisperAudioAdapter:
    FakeWhisper.instances, FakeWhisper.received = 0, []
    return FasterWhisperAudioAdapter(model_factory=FakeWhisper, cache_dir="/tmp/omni-test-models", **kw)


def test_transcribe_defaults_segments_and_lazy_load(tmp_path: Path) -> None:
    w = whisper()
    assert not w.is_loaded
    f = tmp_path / "a.mp3"
    f.write_bytes(b"ID3")
    res = w.transcribe(f, language="ja")
    assert res.text == "猫は1日に12時間眠る" and len(res.segments) == 2 and res.duration_seconds == 5.7
    assert res.meta.provider == "faster-whisper" and res.meta.model == DEFAULT_WHISPER_MODEL == "medium"
    kw = w._model.kw
    assert (kw["model_size_or_path"], kw["device"], kw["compute_type"]) == ("medium", "cpu", "int8")
    source, tkw = FakeWhisper.received[0]
    assert source == str(f) and tkw["vad_filter"] is True and tkw["beam_size"] == 5
    w.transcribe(b"x")
    assert FakeWhisper.instances == 1


def test_transcribe_stream_rewound_and_rejections() -> None:
    w = whisper()
    s = io.BytesIO(b"abcdef")
    w.transcribe(s)
    assert s.tell() == 0
    from omni_ai import UrlRef

    with pytest.raises(UnsupportedFeatureError):
        w.transcribe(UrlRef(url="https://x/a.mp3"))
    with pytest.raises(InvalidRequestError):
        w.transcribe("a.mp3")
    with pytest.raises(UnsupportedFeatureError):
        w.speak("x")


async def test_router_and_client_telemetry(fake_edge) -> None:
    ai = OmniClient(audio=AudioRouter(speech=EdgeTTSAudioAdapter(), transcription=whisper()))
    spoken = await ai.audio.aspeak("こんにちは")
    heard = await ai.audio.atranscribe(spoken.audio_bytes)
    assert heard.text.startswith("猫")
    assert [m.provider for m in ai.telemetry.history] == ["edge-tts", "faster-whisper"]
    assert ai.telemetry.total_cost_usd == 0.0 and ai.telemetry.unpriced_calls == 0
    with pytest.raises(UnsupportedFeatureError):
        AudioRouter(speech=EdgeTTSAudioAdapter()).transcribe(b"x")
    await ai.aclose()
