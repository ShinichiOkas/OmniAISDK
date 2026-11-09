"""1.E: FasterWhisperAudioAdapter の transcribe_iter / on_progress / model_path 試験。

本物の faster-whisper は開かない（モデル 1.5GB・CI で重い）。偽物の WhisperModel で
区間を順に返し、on_progress が 0.0〜1.0 で呼ばれることを確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omni_ai.providers.faster_whisper import FasterWhisperAudioAdapter
from omni_ai.types import TranscriptSegment


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    def __init__(self, duration: float, language: str = "ja") -> None:
        self.duration = duration
        self.language = language
        self.language_probability = 0.99


class _FakeWhisperModel:
    def __init__(self, segments, duration: float, init_kwargs: dict) -> None:
        self._segments = segments
        self._duration = duration
        self.init_kwargs = init_kwargs
        self.calls: list[dict] = []

    def transcribe(self, source, **kwargs):
        self.calls.append({"source": source, **kwargs})
        return iter(self._segments), _FakeInfo(self._duration)


def _factory(segments, duration):
    captured = {}

    def make(**kwargs):
        model = _FakeWhisperModel(segments, duration, kwargs)
        captured["model"] = model
        return model

    return make, captured


# ---------- T1: transcribe_iter ----------


def test_transcribe_iter_yields_segments_in_order() -> None:
    segs = [_FakeSegment(0.0, 2.0, "こんにちは"), _FakeSegment(2.0, 5.0, "世界")]
    factory, _cap = _factory(segs, duration=5.0)
    ad = FasterWhisperAudioAdapter(model="medium", model_factory=factory)
    out = list(ad.transcribe_iter(b"\x00\x00\x00\x00"))
    assert all(isinstance(s, TranscriptSegment) for s in out)
    assert [(s.start, s.end, s.text) for s in out] == [(0.0, 2.0, "こんにちは"), (2.0, 5.0, "世界")]


def test_transcribe_iter_on_progress_is_called_with_fractions() -> None:
    segs = [_FakeSegment(0.0, 2.5, "a"), _FakeSegment(2.5, 5.0, "b"), _FakeSegment(5.0, 10.0, "c")]
    factory, _cap = _factory(segs, duration=10.0)
    ad = FasterWhisperAudioAdapter(model="medium", model_factory=factory)
    progress: list[float] = []
    list(ad.transcribe_iter(b"\x00", on_progress=progress.append))
    assert progress  # 1 回以上呼ばれている
    assert 0.0 <= progress[0] <= 1.0
    assert progress[-1] == 1.0  # 最後に 1.0 が通知される
    assert progress == sorted(progress)  # 単調増加


def test_transcribe_iter_no_progress_when_duration_unknown() -> None:
    segs = [_FakeSegment(0.0, 2.0, "a")]
    factory, _cap = _factory(segs, duration=0.0)  # 長さが取れないケース
    ad = FasterWhisperAudioAdapter(model="medium", model_factory=factory)
    progress: list[float] = []
    list(ad.transcribe_iter(b"\x00", on_progress=progress.append))
    assert progress == []  # 呼ばれない


def test_transcribe_iter_rejects_str_audio() -> None:
    segs: list = []
    factory, _cap = _factory(segs, duration=0.0)
    ad = FasterWhisperAudioAdapter(model="medium", model_factory=factory)
    from omni_ai.exceptions import InvalidRequestError

    with pytest.raises(InvalidRequestError, match="Path"):
        list(ad.transcribe_iter("foo.wav"))  # type: ignore[arg-type]


# ---------- T3: モデルのパス指定 ----------


def test_model_path_is_passed_as_model_size_or_path(tmp_path: Path) -> None:
    # 既存の変換済みモデルのディレクトリを渡す（faster-whisper は model_size_or_path に dir も受ける）
    model_dir = tmp_path / "whisper-medium-ct2"
    model_dir.mkdir()
    factory, cap = _factory([], duration=0.0)
    ad = FasterWhisperAudioAdapter(model=str(model_dir), model_factory=factory)
    ad.load()
    assert cap["model"].init_kwargs["model_size_or_path"] == str(model_dir)


def test_model_name_path_is_passed_in_transcribe(tmp_path: Path) -> None:
    segs = [_FakeSegment(0.0, 1.0, "x")]
    factory, cap = _factory(segs, duration=1.0)
    ad = FasterWhisperAudioAdapter(model="medium", model_factory=factory)
    list(ad.transcribe_iter(b"\x00"))
    assert cap["model"].init_kwargs["model_size_or_path"] == "medium"


# ---------- transcribe（既存）も壊れていない ----------


def test_transcribe_full_still_works() -> None:
    segs = [_FakeSegment(0.0, 1.0, "hello"), _FakeSegment(1.0, 2.0, " world")]
    factory, _cap = _factory(segs, duration=2.0)
    ad = FasterWhisperAudioAdapter(model="medium", model_factory=factory)
    res = ad.transcribe(b"\x00")
    assert res.text == "hello world"
    assert res.duration_seconds == 2.0
    assert len(res.segments) == 2
