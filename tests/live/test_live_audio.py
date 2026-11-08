"""音声の実物テスト。OMNI_LIVE_AUDIO=1 のときだけ実行する。

edge-tts はオンライン、faster-whisper は初回に medium（約 1.5GB）をダウンロードする。
"""

import os

import pytest

pytest.importorskip("edge_tts")
pytest.importorskip("faster_whisper")

from omni_ai import OmniClient
from omni_ai.providers import AudioRouter
from omni_ai.providers.edge_tts import EdgeTTSAudioAdapter
from omni_ai.providers.faster_whisper import FasterWhisperAudioAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OMNI_LIVE_AUDIO"), reason="set OMNI_LIVE_AUDIO=1"),
]


async def test_live_tts_stt_roundtrip_japanese() -> None:
    text = "猫は一日に十二時間から十六時間ほど眠ると言われています。"
    async with OmniClient(
        audio=AudioRouter(speech=EdgeTTSAudioAdapter(), transcription=FasterWhisperAudioAdapter())
    ) as ai:
        spoken = await ai.audio.aspeak(text)
        assert spoken.audio_bytes[:2] in (b"\xff\xf3", b"\xff\xfb", b"ID")  # MP3
        heard = await ai.audio.atranscribe(spoken.audio_bytes, language="ja")
    assert heard.language == "ja"
    assert "猫" in heard.text and "眠る" in heard.text, heard.text


async def test_live_japanese_voices() -> None:
    assert "ja-JP-NanamiNeural" in await EdgeTTSAudioAdapter().alist_voices("ja")
