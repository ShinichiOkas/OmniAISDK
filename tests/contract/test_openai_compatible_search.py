"""OpenAI 互換の検索の口（``POST {base}/search``。前段プロキシの ``/v1/search``）の契約テスト。

応答は ellm-proxy の ``/v1/search`` の形（``{"results": [{"title","url","snippet"}]}``）を手で作った
**合成データ**。本物の上流には一切送らない（respx）。
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from omni_ai import (
    AuthenticationError,
    InvalidRequestError,
    OmniError,
    ProviderConnectionError,
    ProviderOverloadedError,
)
from omni_ai._http import RetryPolicy
from omni_ai.providers.openai_compatible.search import OpenAICompatibleSearchAdapter

BASE = "http://127.0.0.1:9101/v1"
URL = f"{BASE}/search"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("OMNI_OPENAI_COMPATIBLE_API_KEY", raising=False)


def make(**kw) -> OpenAICompatibleSearchAdapter:
    params = {"base_url": BASE, "retry": RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0)}
    params.update(kw)
    return OpenAICompatibleSearchAdapter(**params)


def rows(n: int = 2) -> dict:
    return {
        "results": [
            {"title": f"T{i}", "url": f"https://example.com/{i}", "snippet": f"S{i}"} for i in range(n)
        ]
    }


@respx.mock
def test_default_request_has_no_backend_and_response_is_mapped() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=rows(), headers={"x-request-id": "r1"}))
    res = make().search("mnemo", max_results=3)
    assert json.loads(route.calls.last.request.content) == {"query": "mnemo", "max_results": 3}
    assert [(r.title, r.url, r.content) for r in res.results] == [
        ("T0", "https://example.com/0", "S0"),
        ("T1", "https://example.com/1", "S1"),
    ]
    assert res.meta.provider == "openai-compatible"
    assert res.meta.request_id == "r1"
    assert res.meta.attempts == 1


@respx.mock
@pytest.mark.parametrize("backend", ["ellm", "ddgs"])
def test_backend_is_sent_when_configured_and_named_in_meta(backend: str) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=rows(1)))
    res = make(backend=backend).search("q")
    assert json.loads(route.calls.last.request.content)["backend"] == backend
    assert res.meta.model == backend


@respx.mock
def test_per_call_backend_through_provider_options_overrides() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=rows(1)))
    make(backend="ellm").search("q", **{"openai-compatible": {"backend": "ddgs"}})
    assert json.loads(route.calls.last.request.content)["backend"] == "ddgs"


@respx.mock
def test_content_field_is_read_when_snippet_missing_and_bad_rows_skipped() -> None:
    payload = {
        "results": [
            {"title": "a", "url": "https://a", "content": "C"},
            {"title": "no url"},
            "junk",
            {"title": "b", "url": "https://b", "snippet": "S", "score": 0.5},
        ]
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload))
    res = make().search("q", max_results=10)
    assert [(r.url, r.content, r.score) for r in res.results] == [("https://a", "C", None), ("https://b", "S", 0.5)]


@respx.mock
def test_results_are_capped_at_max_results() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=rows(8)))
    assert len(make().search("q", max_results=3).results) == 3


@respx.mock
@pytest.mark.parametrize(("given", "sent"), [(0, 1), (-5, 1), (500, 50)])
def test_max_results_is_clamped(given: int, sent: int) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=rows(1)))
    make().search("q", max_results=given)
    assert json.loads(route.calls.last.request.content)["max_results"] == sent


@respx.mock
def test_empty_results_is_a_valid_empty_response() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"results": []}))
    assert make().search("q").results == []


@respx.mock
@pytest.mark.parametrize("payload", [{}, {"results": None}, {"results": "x"}, []])
def test_malformed_response_is_an_error(payload) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(OmniError):
        make().search("q")


@respx.mock
@pytest.mark.parametrize(
    ("status", "exc"),
    [(400, InvalidRequestError), (401, AuthenticationError), (502, ProviderOverloadedError)],
)
def test_proxy_errors_raise_and_do_not_fall_back(status: int, exc: type[Exception]) -> None:
    """プロキシの失敗（ellm-proxy は 400/502 を ``{"error":{"message","type"}}`` で返す）は例外のまま。"""
    route = respx.post(URL).mock(
        return_value=httpx.Response(status, json={"error": {"message": "upstream said no", "type": "search_error"}})
    )
    with pytest.raises(exc) as ei:
        make(backend="ellm").search("q")
    assert "upstream said no" in str(ei.value)
    assert all(json.loads(c.request.content)["backend"] == "ellm" for c in route.calls)  # 別の backend で撃ち直さない


@respx.mock
def test_connection_error_is_provider_connection_error() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderConnectionError):
        make(retry=RetryPolicy(max_attempts=1)).search("q")


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_does_not_reach_the_network(query: str) -> None:
    with respx.mock(assert_all_called=False) as m:
        route = m.post(URL).mock(return_value=httpx.Response(200, json=rows()))
        with pytest.raises(InvalidRequestError):
            make().search(query)
        assert route.call_count == 0


@respx.mock
def test_key_from_shared_env_var_and_absent_without(monkeypatch: pytest.MonkeyPatch) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=rows(1)))
    make().search("q")
    assert "authorization" not in route.calls.last.request.headers
    monkeypatch.setenv("OMNI_OPENAI_COMPATIBLE_API_KEY", "sk-shared")
    make().search("q")
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-shared"


def test_base_url_is_required_and_normalized() -> None:
    with pytest.raises(InvalidRequestError):
        OpenAICompatibleSearchAdapter()
    assert OpenAICompatibleSearchAdapter(base_url="http://h:2").base_url == "http://h:2/v1"


def test_adapter_name_does_not_mention_ellm() -> None:
    assert "ellm" not in OpenAICompatibleSearchAdapter.__name__.lower()
    assert "ellm" not in OpenAICompatibleSearchAdapter.provider_name.lower()
    assert "ellm" not in OpenAICompatibleSearchAdapter.__module__.lower()


@respx.mock
async def test_async_search_with_backend() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=rows()))
    res = await make(backend="ddgs").asearch("q", max_results=2)
    assert json.loads(route.calls.last.request.content) == {"query": "q", "max_results": 2, "backend": "ddgs"}
    assert len(res.results) == 2


@respx.mock
async def test_async_error_mapping() -> None:
    respx.post(URL).mock(return_value=httpx.Response(502, json={"error": {"message": "x"}}))
    with pytest.raises(ProviderOverloadedError):
        await make(retry=RetryPolicy(max_attempts=1)).asearch("q")
