"""実 API / 実モデルでの埋め込み。環境変数で有効化する。

- OMNI_LIVE_GEMINI=1（GEMINI_API_KEY）
- OMNI_LIVE_OLLAMA_EMBED=<model>（例: nomic-embed-text）
- OMNI_LIVE_FASTEMBED=1。OMNI_LIVE_FASTEMBED_CACHE で cache_dir を指定（既存のモデルキャッシュを共有する場合）
"""

import math
import os
from pathlib import Path

import pytest

from omni_ai.providers.fastembed import FastEmbedEmbeddingAdapter
from omni_ai.providers.gemini.embedding import GeminiEmbeddingAdapter
from omni_ai.providers.ollama.embedding import OllamaEmbeddingAdapter

pytestmark = pytest.mark.live
TEXTS = ["猫が窓辺で眠っている", "A cat is sleeping by the window", "株価が急落した"]


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def check_semantics(vectors: list[list[float]]) -> None:
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-5 for v in vectors)
    # 同じ意味の日英ペアは、無関係な文より近い
    assert cos(vectors[0], vectors[1]) > cos(vectors[0], vectors[2])


@pytest.mark.skipif(not os.environ.get("OMNI_LIVE_GEMINI"), reason="set OMNI_LIVE_GEMINI=1")
def test_live_gemini_embedding() -> None:
    res = GeminiEmbeddingAdapter().embed(TEXTS, dimensions=768, task="document")
    assert res.dimensions == 768 and res.meta.usage.prompt_tokens > 0
    check_semantics(res.vectors)


@pytest.mark.skipif(not os.environ.get("OMNI_LIVE_OLLAMA_EMBED"), reason="set OMNI_LIVE_OLLAMA_EMBED=<model>")
async def test_live_ollama_embedding() -> None:
    a = OllamaEmbeddingAdapter(default_model=os.environ["OMNI_LIVE_OLLAMA_EMBED"])
    res = await a.aembed(TEXTS)
    assert res.meta.usage.prompt_tokens > 0
    assert len(res.vectors) == 3
    await a.aclose()


@pytest.mark.skipif(not os.environ.get("OMNI_LIVE_FASTEMBED"), reason="set OMNI_LIVE_FASTEMBED=1")
def test_live_fastembed_embedding() -> None:
    cache = os.environ.get("OMNI_LIVE_FASTEMBED_CACHE")
    a = FastEmbedEmbeddingAdapter(
        cache_dir=Path(cache).expanduser() if cache else None, local_files_only=bool(cache)
    )
    res = a.embed(TEXTS)
    assert res.dimensions == 384
    check_semantics(res.vectors)
