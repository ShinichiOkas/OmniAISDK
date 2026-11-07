"""キー不要の RAG パイプライン（ddgs 検索 → fastembed 埋め込み → fastembed rerank）を実物で通す。

OMNI_LIVE_RAG=1 のときだけ実行する（初回は rerank モデル約 0.6GB をダウンロードする）。
OMNI_LIVE_FASTEMBED_CACHE を設定すると、埋め込みモデルはそのキャッシュを使う（再ダウンロードしない）。
"""

import os
from pathlib import Path

import pytest

from omni_ai import OmniClient
from omni_ai.providers.ddgs import DDGSSearchAdapter
from omni_ai.providers.fastembed import FastEmbedEmbeddingAdapter
from omni_ai.providers.fastembed.rerank import FastEmbedRerankAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OMNI_LIVE_RAG"), reason="set OMNI_LIVE_RAG=1"),
]


def _embedder() -> FastEmbedEmbeddingAdapter:
    cache = os.environ.get("OMNI_LIVE_FASTEMBED_CACHE")
    if cache:
        return FastEmbedEmbeddingAdapter(cache_dir=Path(cache).expanduser(), local_files_only=True)
    return FastEmbedEmbeddingAdapter()


def test_live_rerank_multilingual() -> None:
    r = FastEmbedRerankAdapter()
    res = r.rerank(
        "猫の睡眠時間はどれくらい？",
        [
            "株価は本日急落した。",
            "Cats sleep about 12 to 16 hours a day.",
            "猫は1日に12〜16時間ほど眠る。",
        ],
    )
    assert {res.results[0].index, res.results[1].index} == {1, 2}  # 日英どちらの関連文書も上位
    assert res.results[-1].index == 0 and res.results[-1].relevance_score < 0.01


async def test_live_keyless_rag_pipeline() -> None:
    query = "Python の最新の安定版"
    async with OmniClient(
        search=DDGSSearchAdapter(), embedding=_embedder(), rerank=FastEmbedRerankAdapter()
    ) as ai:
        hits = await ai.search.asearch(query, max_results=8)
        docs = [f"{h.title}\n{h.content}" for h in hits.results]
        assert docs
        emb = await ai.embedding.aembed([query, *docs], task="document")
        assert len(emb.vectors) == len(docs) + 1
        ranked = await ai.rerank.arerank(query, docs, top_n=3)
        assert len(ranked.results) == min(3, len(docs))
        assert ranked.results[0].relevance_score >= ranked.results[-1].relevance_score
        providers = [m.provider for m in ai.telemetry.history]
        assert providers == ["ddgs", "fastembed", "fastembed"], providers
        assert ai.telemetry.total_cost_usd == 0.0 and ai.telemetry.unpriced_calls == 0
