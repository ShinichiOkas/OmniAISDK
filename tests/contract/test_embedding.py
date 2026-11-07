"""Embedding の契約テスト。Gemini / Ollama は実 API の記録、ローカルは偽のモデルで検証する。"""

import json
import math

import httpx
import pytest
import respx

from omni_ai import (
    ContextLengthExceededError,
    InvalidRequestError,
    NotFoundError,
    OmniClient,
    UnsupportedFeatureError,
)
from omni_ai._http import RetryPolicy
from omni_ai.providers.gemini.embedding import DEFAULT_EMBEDDING_MODEL, GeminiEmbeddingAdapter
from omni_ai.providers.local import DEFAULT_LOCAL_MODEL, LocalEmbeddingAdapter
from omni_ai.providers.ollama.embedding import OllamaEmbeddingAdapter

GBASE = "https://gemini.test/v1beta"
OBASE = "http://ollama.test:11434"


def norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def gemini(**kw) -> GeminiEmbeddingAdapter:
    return GeminiEmbeddingAdapter(
        api_key="k",
        base_url=GBASE,
        default_model="gemini-embedding-001",
        retry=RetryPolicy(max_attempts=1),
        **kw,
    )


def ollama(**kw) -> OllamaEmbeddingAdapter:
    return OllamaEmbeddingAdapter(
        base_url=OBASE, default_model="nomic-embed-text", retry=RetryPolicy(max_attempts=1), **kw
    )


SHOW_EMBED = {"capabilities": ["embedding"]}

# ------------------------------------------------------------------ Gemini


@respx.mock
def test_gemini_batch_dims_task_and_normalize(fixture_bytes) -> None:
    route = respx.post(f"{GBASE}/models/gemini-embedding-001:batchEmbedContents").mock(
        return_value=httpx.Response(200, content=fixture_bytes("gemini_embed", "batch.json"))
    )
    res = gemini().embed(["apple", "banana", "東京"], dimensions=256, task="document")
    assert len(res.vectors) == 3 and res.dimensions == 256 and res.normalized
    assert all(abs(norm(v) - 1.0) < 1e-6 for v in res.vectors)
    body = json.loads(route.calls[0].request.content)
    assert body["requests"][0] == {
        "model": "models/gemini-embedding-001",
        "content": {"parts": [{"text": "apple"}]},
        "outputDimensionality": 256,
        "taskType": "RETRIEVAL_DOCUMENT",
    }


@respx.mock
def test_gemini_splits_batches_and_sums_usage() -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["requests"])
        return httpx.Response(
            200, json={"embeddings": [{"values": [1.0, 0.0]}] * n, "usageMetadata": {"promptTokenCount": n}}
        )

    route = respx.post(f"{GBASE}/models/gemini-embedding-001:batchEmbedContents").mock(side_effect=reply)
    res = gemini().embed([f"t{i}" for i in range(250)], normalize=False)
    assert route.call_count == 3 and len(res.vectors) == 250
    assert res.meta.usage.prompt_tokens == 250 and not res.normalized


def test_gemini_default_model() -> None:
    assert DEFAULT_EMBEDDING_MODEL == "gemini-embedding-2"


@respx.mock
def test_gemini_empty_handling() -> None:
    route = respx.post(f"{GBASE}/models/gemini-embedding-001:batchEmbedContents")
    assert gemini().embed([]).vectors == []
    with pytest.raises(InvalidRequestError, match="empty"):
        gemini().embed(["ok", "  "])
    assert route.call_count == 0


@respx.mock
def test_gemini_recorded_400_maps(fixture_bytes) -> None:
    respx.post(f"{GBASE}/models/gemini-embedding-001:batchEmbedContents").mock(
        return_value=httpx.Response(400, content=fixture_bytes("gemini_embed", "err_empty.json"))
    )
    with pytest.raises(InvalidRequestError):
        gemini().embed("x")


# ------------------------------------------------------------------ Ollama


@respx.mock
async def test_ollama_batch_recorded(fixture_bytes) -> None:
    respx.post(f"{OBASE}/api/show").mock(return_value=httpx.Response(200, json=SHOW_EMBED))
    route = respx.post(f"{OBASE}/api/embed").mock(
        return_value=httpx.Response(200, content=fixture_bytes("ollama_embed", "batch.json"))
    )
    a = ollama()
    res = await a.aembed(["apple", "banana", "東京"], task="query")
    assert len(res.vectors) == 3 and res.dimensions == 768
    assert res.meta.usage.prompt_tokens == 10 and res.meta.model == "nomic-embed-text"
    assert all(abs(norm(v) - 1.0) < 1e-6 for v in res.vectors)
    assert json.loads(route.calls[0].request.content) == {
        "model": "nomic-embed-text",
        "input": ["apple", "banana", "東京"],
        "truncate": True,
    }
    await a.aclose()


@respx.mock
def test_ollama_count_mismatch_is_error() -> None:
    # 実測: 空入力に 200 で embeddings: [] を返す。件数のずれは黙って通さない
    respx.post(f"{OBASE}/api/embed").mock(return_value=httpx.Response(200, json={"embeddings": []}))
    with pytest.raises(Exception, match="0 vectors for 1 inputs"):
        ollama(check_capabilities=False).embed("x")


@respx.mock
def test_ollama_chat_model_rejected_before_send() -> None:
    respx.post(f"{OBASE}/api/show").mock(
        return_value=httpx.Response(200, json={"capabilities": ["completion", "tools"]})
    )
    embed = respx.post(f"{OBASE}/api/embed")
    with pytest.raises(UnsupportedFeatureError, match="not an embedding model"):
        ollama().embed("x", model="gemma4:31b-cloud")
    assert embed.call_count == 0


@pytest.mark.parametrize(
    ("name", "status", "exc"),
    [
        ("err_missing", 404, NotFoundError),
        ("long_notrunc", 400, ContextLengthExceededError),
    ],
)
@respx.mock
def test_ollama_recorded_errors(fixture_bytes, name: str, status: int, exc: type) -> None:
    respx.post(f"{OBASE}/api/embed").mock(
        return_value=httpx.Response(status, content=fixture_bytes("ollama_embed", f"{name}.json"))
    )
    with pytest.raises(exc):
        ollama(check_capabilities=False, truncate=False).embed("x")


# ------------------------------------------------------------------ Local (fastembed を偽物に差し替え)


class FakeTextEmbedding:
    instances = 0

    def __init__(self, **kwargs) -> None:
        FakeTextEmbedding.instances += 1
        self.kwargs = kwargs

    def embed(self, texts, batch_size=64):
        for t in texts:
            yield [float(len(t)), 3.0, 4.0]


def local(**kw) -> LocalEmbeddingAdapter:
    return LocalEmbeddingAdapter(model_factory=FakeTextEmbedding, cache_dir="/tmp/omni-test-models", **kw)


def test_local_lazy_load_normalize_and_mnemo_defaults(tmp_path) -> None:
    FakeTextEmbedding.instances = 0
    a = LocalEmbeddingAdapter(model_factory=FakeTextEmbedding, cache_dir=tmp_path, threads=2)
    assert not a.is_loaded
    res = a.embed(["ab", "abcd"])
    assert a.is_loaded and FakeTextEmbedding.instances == 1
    a.embed("again")
    assert FakeTextEmbedding.instances == 1  # 1 回だけ読み込む
    assert res.meta.provider == "local" and res.meta.model == DEFAULT_LOCAL_MODEL
    assert DEFAULT_LOCAL_MODEL == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert all(abs(norm(v) - 1.0) < 1e-9 for v in res.vectors) and res.normalized
    assert a._model.kwargs["cache_dir"] == str(tmp_path) and a._model.kwargs["threads"] == 2


def test_local_rejects_other_model_and_dimensions() -> None:
    with pytest.raises(UnsupportedFeatureError):
        local().embed("x", model="BAAI/bge-small-en-v1.5")
    with pytest.raises(UnsupportedFeatureError, match="fixed 3"):
        local().embed("x", dimensions=2)
    with pytest.raises(InvalidRequestError):
        local().embed([""])


async def test_local_async_and_client_telemetry() -> None:
    ai = OmniClient(embedding=local())
    res = await ai.embedding.aembed(["a", "bb"])
    assert len(res.vectors) == 2
    assert ai.telemetry.history[0].provider == "local" and ai.telemetry.history[0].estimated_cost_usd == 0.0
    assert ai.telemetry.unpriced_calls == 0
    await ai.aclose()
