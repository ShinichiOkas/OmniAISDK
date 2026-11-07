import io
import json
import random
from pathlib import Path

import httpx
import pytest
import respx
from hypothesis import given
from hypothesis import strategies as st

from omni_ai._http import AsyncTransport, NDJSONDecoder, RetryPolicy, SSEDecoder, SyncTransport
from omni_ai._media import b64_size, load_bytes, to_base64
from omni_ai.exceptions import (
    InvalidRequestError,
    OmniError,
    ProviderConnectionError,
    ProviderOverloadedError,
    RateLimitError,
    UnsupportedFeatureError,
)

# ---------------------------------------------------------------- RetryPolicy


def test_retry_policy_respects_retryable_and_attempts() -> None:
    p = RetryPolicy(max_attempts=3, base_delay=1.0, jitter=0.0)
    over = ProviderOverloadedError("x", provider="p")
    assert p.next_delay(1, over) == 1.0
    assert p.next_delay(2, over) == 2.0
    assert p.next_delay(3, over) is None
    assert p.next_delay(1, InvalidRequestError("x", provider="p")) is None


def test_retry_policy_retry_after() -> None:
    p = RetryPolicy(max_attempts=3, max_delay=5.0)
    assert p.next_delay(1, RateLimitError("x", provider="p", retry_after_seconds=2.0)) == 2.0
    assert p.next_delay(1, RateLimitError("x", provider="p", retry_after_seconds=60.0)) is None


def test_retry_policy_jitter_bounds() -> None:
    p = RetryPolicy(max_attempts=5, base_delay=1.0, jitter=0.25)
    rng = random.Random(0)
    for _ in range(100):
        d = p.next_delay(1, ProviderOverloadedError("x", provider="p"), rng)
        assert d is not None and 0.75 <= d <= 1.25


# ---------------------------------------------------------------- decoders

SSE_PAYLOAD = (
    b': comment\r\ndata: {"a": 1}\r\n\r\nevent: custom\ndata: line1\ndata: line2\n\n'
) + 'data: {"t": "日本語"}\n\n'.encode()


def _split(data: bytes, cuts: list[int]) -> list[bytes]:
    points = sorted({c % (len(data) + 1) for c in cuts})
    out, prev = [], 0
    for p in points:
        out.append(data[prev:p])
        prev = p
    out.append(data[prev:])
    return out


@given(st.lists(st.integers(min_value=0, max_value=10_000), max_size=20))
def test_sse_decoder_is_split_invariant(cuts: list[int]) -> None:
    dec = SSEDecoder()
    events = []
    for chunk in _split(SSE_PAYLOAD, cuts):
        events.extend(dec.feed(chunk))
    events.extend(dec.flush())
    assert [e.data for e in events] == ['{"a": 1}', "line1\nline2", '{"t": "日本語"}']
    assert events[1].event == "custom"
    assert events[2].json() == {"t": "日本語"}


def test_sse_decoder_flushes_unterminated_event() -> None:
    dec = SSEDecoder()
    assert dec.feed(b"data: x") == []
    assert [e.data for e in dec.flush()] == ["x"]


NDJSON_PAYLOAD = (
    "".join(
        json.dumps(o, ensure_ascii=False) + "\n" for o in [{"a": 1}, {"msg": "こんにちは"}, {"done": True}]
    ).encode()
    + b"\n"
)


@given(st.lists(st.integers(min_value=0, max_value=10_000), max_size=20))
def test_ndjson_decoder_is_split_invariant(cuts: list[int]) -> None:
    dec = NDJSONDecoder()
    out = []
    for chunk in _split(NDJSON_PAYLOAD, cuts):
        out.extend(dec.feed(chunk))
    out.extend(dec.flush())
    assert out == [{"a": 1}, {"msg": "こんにちは"}, {"done": True}]


# ---------------------------------------------------------------- transport


def _mapper(status: int, body: bytes, headers: object) -> OmniError:
    if status == 429:
        return RateLimitError("rl", provider="t")
    if status >= 500:
        return ProviderOverloadedError("over", provider="t")
    return InvalidRequestError(body.decode(), provider="t")


def _sync(**kw: object) -> SyncTransport:
    return SyncTransport(
        provider="t",
        base_url="https://api.test/v1/",
        error_mapper=_mapper,
        retry=RetryPolicy(max_attempts=3, base_delay=0.0, jitter=0.0),
        sleep=lambda _s: None,
        **kw,
    )  # type: ignore[arg-type]


@respx.mock
def test_sync_transport_retries_then_succeeds() -> None:
    route = respx.post("https://api.test/v1/x").mock(
        side_effect=[httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    t = _sync(headers={"x-api-key": "secret"})
    res = t.request("POST", "/x", json={"q": 1})
    assert res.json() == {"ok": 1} and res.attempts == 3
    assert route.calls[0].request.headers["user-agent"].startswith("omni-ai-sdk/")
    assert route.calls[0].request.headers["x-api-key"] == "secret"


@respx.mock
def test_sync_transport_raises_mapped_error_without_secret_headers() -> None:
    respx.post("https://api.test/v1/x").mock(
        return_value=httpx.Response(400, text="bad", headers={"x-goog-api-key": "s", "x-req": "1"})
    )
    with pytest.raises(InvalidRequestError) as ei:
        _sync().request("POST", "x")
    assert ei.value.status_code == 400
    assert (
        ei.value.raw_error["headers"] == {"x-req": "1", "content-length": "3"}
        or "x-goog-api-key" not in ei.value.raw_error["headers"]
    )


@respx.mock
def test_sync_transport_connection_error_is_retryable_and_mapped() -> None:
    respx.get("https://api.test/v1/x").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderConnectionError):
        _sync().request("GET", "x")
    assert respx.calls.call_count == 3


@respx.mock
def test_sync_stream_and_close() -> None:
    respx.post("https://api.test/v1/s").mock(return_value=httpx.Response(200, content=b"a\nb\n"))
    t = _sync()
    with t.stream("POST", "s") as h:
        assert b"".join(t.iter_bytes(h)) == b"a\nb\n"
    t.close()
    with pytest.raises(OmniError, match="closed"):
        t.request("GET", "x")


@respx.mock
async def test_async_transport_retry_and_stream() -> None:
    respx.post("https://api.test/v1/s").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, content=b"xyz")]
    )

    async def nosleep(_s: float) -> None:
        return None

    t = AsyncTransport(
        provider="t",
        base_url="https://api.test/v1",
        error_mapper=_mapper,
        retry=RetryPolicy(max_attempts=2, base_delay=0.0),
        sleep=nosleep,
    )
    async with t.stream("POST", "s") as h:
        assert h.attempts == 2
        data = b"".join([c async for c in t.aiter_bytes(h)])
    assert data == b"xyz"
    await t.aclose()


def test_injected_client_is_not_closed() -> None:
    client = httpx.Client()
    t = _sync(client=client)
    t.close()
    assert not client.is_closed
    client.close()


# ---------------------------------------------------------------- media


def test_load_bytes_limits(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    p.write_bytes(b"x" * 10)
    assert load_bytes(p, limit=10, provider="t") == b"x" * 10
    with pytest.raises(UnsupportedFeatureError):
        load_bytes(p, limit=9, provider="t")
    with pytest.raises(UnsupportedFeatureError):
        load_bytes(io.BytesIO(b"y" * 20), limit=5, provider="t")


class _Unseekable(io.RawIOBase):
    def __init__(self, n: int) -> None:
        self.left = n

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        raise OSError("unseekable")

    def read(self, size: int = -1) -> bytes:
        n = self.left if size < 0 else min(size, self.left)
        self.left -= n
        return b"z" * n


def test_load_bytes_unseekable_stream_stops_at_limit() -> None:
    with pytest.raises(UnsupportedFeatureError):
        load_bytes(_Unseekable(5 * 1024 * 1024), limit=1024 * 1024, provider="t")
    assert load_bytes(_Unseekable(10), limit=100, provider="t") == b"z" * 10


def test_to_base64_limit_is_on_encoded_size() -> None:
    assert b64_size(3) == 4 and b64_size(4) == 8
    assert to_base64(b"abc", limit=4, provider="t") == "YWJj"
    with pytest.raises(UnsupportedFeatureError):
        to_base64(b"abcd", limit=4, provider="t")
