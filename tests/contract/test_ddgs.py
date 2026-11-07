"""DDGSSearchAdapter の契約テスト。結果は実物の記録（fixtures/ddgs）、例外は ddgs と同名の偽物で再現する。"""

import json
from typing import ClassVar

import pytest

from omni_ai import (
    InvalidRequestError,
    OmniClient,
    ProviderOverloadedError,
    ProviderTimeoutError,
    RateLimitError,
)
from omni_ai._http import RetryPolicy
from omni_ai.providers.ddgs import DDGSSearchAdapter


class RatelimitException(Exception):
    pass


class TimeoutException(Exception):
    pass


class DDGSException(Exception):
    pass


class FakeDDGS:
    script: ClassVar[list] = []
    calls: ClassVar[list] = []
    init_kwargs: ClassVar[list] = []

    def __init__(self, **kwargs) -> None:
        FakeDDGS.init_kwargs.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def text(self, query, **kwargs):
        FakeDDGS.calls.append({"query": query, **kwargs})
        item = FakeDDGS.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake():
    FakeDDGS.script, FakeDDGS.calls, FakeDDGS.init_kwargs = [], [], []
    return FakeDDGS


def adapter(**kw) -> DDGSSearchAdapter:
    return DDGSSearchAdapter(
        ddgs_factory=FakeDDGS, sleep=lambda _s: None, retry=RetryPolicy(max_attempts=2, base_delay=0.0), **kw
    )


def test_recorded_results_mapping_and_defaults(fake, fixture_bytes) -> None:
    raw = json.loads(fixture_bytes("ddgs", "text_ja.json"))
    fake.script = [raw]
    res = adapter().search("Python 最新版 リリース", max_results=5)
    assert len(res.results) == len(raw)
    assert res.results[0].url == raw[0]["href"] and res.results[0].content == raw[0]["body"]
    assert res.meta.provider == "ddgs" and res.meta.model == "auto" and res.meta.estimated_cost_usd == 0.0
    call = fake.calls[0]
    assert (call["region"], call["safesearch"], call["backend"], call["max_results"]) == (
        "ja-jp",
        "on",
        "auto",
        5,
    )


def test_dedupe_clamp_and_options(fake) -> None:
    fake.script = [
        [
            {"title": "a", "href": "https://x/1", "body": "1"},
            {"title": "a2", "href": "https://x/1", "body": "dup"},
            {"title": "b", "href": "", "body": "no url"},
            {"title": "c", "href": "https://x/2", "body": "2"},
        ]
    ]
    res = adapter(proxy="socks5://p:1").search(
        "q", max_results=999, ddgs={"region": "us-en", "timelimit": "w"}, gemini={"x": 1}
    )
    assert [r.url for r in res.results] == ["https://x/1", "https://x/2"]
    assert (
        fake.calls[0]["max_results"] == 50
        and fake.calls[0]["region"] == "us-en"
        and fake.calls[0]["timelimit"] == "w"
    )
    assert fake.init_kwargs[0] == {"timeout": 10, "proxy": "socks5://p:1"}


def test_no_results_is_empty_not_error(fake) -> None:
    fake.script = [DDGSException("No results found.")]
    assert adapter().search("zzz").results == []


@pytest.mark.parametrize(
    ("exc", "expected", "calls"),
    [
        (RatelimitException("202 Ratelimit"), RateLimitError, 2),
        (TimeoutException("timed out"), ProviderTimeoutError, 2),
        (DDGSException("https://... 403 Forbidden"), ProviderOverloadedError, 2),
    ],
)
def test_error_mapping_with_retry(fake, exc, expected, calls) -> None:
    fake.script = [exc, exc]
    with pytest.raises(expected):
        adapter().search("q")
    assert len(fake.calls) == calls


def test_retry_then_success(fake) -> None:
    fake.script = [RatelimitException("rl"), [{"title": "t", "href": "https://x", "body": "b"}]]
    res = adapter().search("q")
    assert len(res.results) == 1 and res.meta.attempts == 2


def test_empty_query_rejected(fake) -> None:
    with pytest.raises(InvalidRequestError):
        adapter().search("  ")
    assert fake.calls == []


async def test_async_and_client_telemetry(fake) -> None:
    fake.script = [[{"title": "t", "href": "https://x", "body": "b"}]]
    ai = OmniClient(search=adapter())
    res = await ai.search.asearch("q")
    assert res.results[0].title == "t"
    assert ai.telemetry.history[0].provider == "ddgs" and ai.telemetry.unpriced_calls == 0
    await ai.aclose()
