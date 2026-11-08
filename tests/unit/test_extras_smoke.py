"""extras のエンジンが、この OS にインストールでき、読み込めるかを確かめる。

モデルのダウンロードやネットワークは使わない。

CI では OMNI_REQUIRE_EXTRAS=1 を設定し、入っていなければ失敗にする。手元では入っていなければスキップする。
"""

import importlib
import os

import pytest

REQUIRED = bool(os.environ.get("OMNI_REQUIRE_EXTRAS"))

# (extras 名, モジュール, 確かめる属性)
ENGINES = [
    ("local", "fastembed", "TextEmbedding"),
    ("local", "fastembed.rerank.cross_encoder", "TextCrossEncoder"),
    ("local", "onnxruntime", "InferenceSession"),
    ("search", "ddgs", "DDGS"),
    ("docs", "pypdf", "PdfReader"),
    ("docs", "markitdown", "MarkItDown"),
    ("mcp", "mcp", "ClientSession"),
    ("audio", "edge_tts", "Communicate"),
    ("audio", "faster_whisper", "WhisperModel"),
    ("audio", "ctranslate2", "Generator"),
    ("audio", "av", "open"),
]


@pytest.mark.parametrize(("extra", "module", "attr"), ENGINES, ids=[m for _, m, _ in ENGINES])
def test_engine_importable(extra: str, module: str, attr: str) -> None:
    try:
        mod = importlib.import_module(module)
    except ImportError as exc:
        if REQUIRED:
            pytest.fail(f"[{extra}] {module} could not be imported on this OS: {exc}")
        pytest.skip(f"[{extra}] {module} is not installed")
    assert hasattr(mod, attr)


def test_onnxruntime_has_cpu_provider() -> None:
    ort = pytest.importorskip("onnxruntime") if not REQUIRED else importlib.import_module("onnxruntime")
    assert "CPUExecutionProvider" in ort.get_available_providers()


def test_adapters_construct_without_network() -> None:
    """アダプターは作るだけではモデルを読み込まず、ネットワークにも出ない。"""
    from omni_ai.providers import (
        DDGSSearchAdapter,
        DockerSandboxAdapter,
        EdgeTTSAudioAdapter,
        FastEmbedEmbeddingAdapter,
        FastEmbedRerankAdapter,
        FasterWhisperAudioAdapter,
        default_document_parser,
    )

    assert not FastEmbedEmbeddingAdapter().is_loaded
    assert not FastEmbedRerankAdapter().is_loaded
    assert not FasterWhisperAudioAdapter().is_loaded
    EdgeTTSAudioAdapter()
    DDGSSearchAdapter()
    DockerSandboxAdapter(docker_cmd="docker")
    default_document_parser()
