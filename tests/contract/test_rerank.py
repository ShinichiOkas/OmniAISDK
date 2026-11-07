"""FastEmbedRerankAdapter の契約テスト（クロスエンコーダーを偽物に差し替え）。"""

import pytest

from omni_ai import InvalidRequestError, OmniClient, OmniError, UnsupportedFeatureError
from omni_ai.providers.fastembed.rerank import DEFAULT_RERANK_MODEL, FastEmbedRerankAdapter


class FakeCrossEncoder:
    instances = 0

    def __init__(self, **kwargs) -> None:
        FakeCrossEncoder.instances += 1
        self.kwargs = kwargs

    def rerank(self, query, documents, batch_size=64):
        # 実測に近いロジット: query の語を含む文書は +7、それ以外は -11
        return [7.0 if query.split()[0] in d else -11.0 for d in documents]


def adapter(**kw) -> FastEmbedRerankAdapter:
    return FastEmbedRerankAdapter(model_factory=FakeCrossEncoder, cache_dir="/tmp/omni-test-models", **kw)


def test_sorted_sigmoid_scores_top_n_and_lazy_load() -> None:
    FakeCrossEncoder.instances = 0
    a = adapter(threads=2)
    assert not a.is_loaded
    docs = ["株価が急落", "猫 は よく眠る", "犬 の 散歩", "猫 の 睡眠"]
    res = a.rerank("猫 睡眠", docs, top_n=2)
    assert [r.index for r in res.results] == [1, 3]
    assert all(0.99 < r.relevance_score < 1.0 for r in res.results)
    assert res.results[0].document == docs[1]
    assert res.meta.provider == "fastembed" and res.meta.model == DEFAULT_RERANK_MODEL
    a.rerank("猫", ["猫"])
    assert FakeCrossEncoder.instances == 1 and a._lazy._model.kwargs["threads"] == 2


def test_raw_scores_option() -> None:
    res = adapter().rerank("猫", ["猫だ", "犬だ"], fastembed={"raw_scores": True}, ddgs={"x": 1})
    assert [r.relevance_score for r in res.results] == [7.0, -11.0]


def test_validation() -> None:
    a = adapter()
    assert a.rerank("q", []).results == []
    with pytest.raises(InvalidRequestError):
        a.rerank(" ", ["d"])
    with pytest.raises(InvalidRequestError):
        a.rerank("q", ["ok", ""])
    with pytest.raises(InvalidRequestError):
        a.rerank("q", ["d"], top_n=0)
    with pytest.raises(UnsupportedFeatureError):
        a.rerank("q", ["d"], model="Xenova/ms-marco-MiniLM-L-6-v2")
    a.close()
    with pytest.raises(OmniError, match="closed"):
        a.rerank("q", ["d"])


async def test_async_and_client_telemetry() -> None:
    ai = OmniClient(rerank=adapter())
    res = await ai.rerank.arerank("猫", ["猫", "犬"])
    assert res.results[0].index == 0
    assert ai.telemetry.history[0].provider == "fastembed" and ai.telemetry.unpriced_calls == 0
    await ai.aclose()
