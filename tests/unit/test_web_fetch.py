"""HttpxWebFetchAdapter の単体試験。respx で HTTP を偽物にして確かめる。"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from omni_ai.exceptions import ProviderConnectionError
from omni_ai.providers.web_fetch import HttpxWebFetchAdapter, ProviderError, _score_image

_HTML = """<!doctype html>
<html><head><title>Hello World</title></head>
<body>
  <nav>skip me</nav>
  <article>
    <h1>Hello</h1>
    <p>This is a long enough body paragraph to pass readability's heuristics
    so that the article is extracted correctly for us.</p>
    <p>Another decent paragraph that keeps readability happy about the article's text density.</p>
    <img src="/img/content.jpg" alt="a nice photograph of the subject">
    <img src="/img/logo.svg" alt="logo">
    <a href="https://example.net/outbound">outbound</a>
  </article>
</body></html>
"""


@respx.mock
def test_fetch_extracts_markdown_and_images():
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )
    adapter = HttpxWebFetchAdapter()
    try:
        page = adapter.fetch("https://example.com/page")
    finally:
        adapter.close()
    assert "Hello" in page.markdown
    assert page.title.lower().startswith("hello")
    assert page.final_url == "https://example.com/page"
    assert page.mime_type == "text/html"
    # 本文画像のほうが装飾（logo.svg）より上位にある
    assert page.images
    assert page.images[0].url.endswith("content.jpg")
    assert page.images_total >= 2
    assert "https://example.net/outbound" in page.links
    assert page.truncated is False
    assert page.raw_bytes_size > 0


@respx.mock
def test_fetch_truncates_by_max_length():
    body = "<html><body>" + "<p>spam more text</p>" * 500 + "</body></html>"
    respx.get("https://example.com/long").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/html"})
    )
    adapter = HttpxWebFetchAdapter()
    try:
        page = adapter.fetch("https://example.com/long", max_length=200)
    finally:
        adapter.close()
    assert len(page.markdown) <= 200
    assert page.truncated is True


@respx.mock
def test_fetch_follows_redirects_and_runs_filter_each_hop():
    respx.get("https://example.com/a").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.com/b"})
    )
    respx.get("https://example.com/b").mock(
        return_value=httpx.Response(302, headers={"location": "https://evil.example/c"})
    )
    respx.get("https://evil.example/c").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )

    seen: list[str] = []

    def filt(url: str) -> str | None:
        seen.append(url)
        if url.startswith("https://evil."):
            return "host is denied"
        return None

    adapter = HttpxWebFetchAdapter(url_filter=filt)
    try:
        with pytest.raises(ProviderError, match="host is denied"):
            adapter.fetch("https://example.com/a")
    finally:
        adapter.close()
    # 各リダイレクト先が url_filter に渡っている
    assert "https://example.com/a" in seen
    assert "https://example.com/b" in seen
    assert "https://evil.example/c" in seen


@respx.mock
def test_fetch_url_filter_blocks_initial_url():
    def filt(url: str) -> str | None:
        return "blocked" if "private" in url else None

    adapter = HttpxWebFetchAdapter(url_filter=filt)
    try:
        with pytest.raises(ProviderError, match="blocked"):
            adapter.fetch("https://private.example/x")
    finally:
        adapter.close()


@respx.mock
def test_fetch_images_downloads_bytes():
    img = b"\x89PNG\r\n\x1a\n" + b"x" * 32  # 本物の PNG ではないが、byte 列として扱えば十分
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )
    respx.get("https://example.com/img/content.jpg").mock(
        return_value=httpx.Response(200, content=img, headers={"content-type": "image/png"})
    )
    respx.get("https://example.com/img/logo.svg").mock(
        return_value=httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"})
    )
    adapter = HttpxWebFetchAdapter()
    try:
        page = adapter.fetch("https://example.com/page", fetch_images=True)
    finally:
        adapter.close()
    # 1 枚目（本文画像）はダウンロードされている
    assert page.images[0].data_base64 is not None
    assert base64.b64decode(page.images[0].data_base64) == img
    assert page.images[0].mime_type == "image/png"


@respx.mock
def test_fetch_document_mime_leaves_markdown_empty():
    pdf_bytes = b"%PDF-1.4\n" + b"x" * 200
    respx.get("https://example.com/x.pdf").mock(
        return_value=httpx.Response(200, content=pdf_bytes, headers={"content-type": "application/pdf"})
    )
    adapter = HttpxWebFetchAdapter()
    try:
        page = adapter.fetch("https://example.com/x.pdf")
    finally:
        adapter.close()
    assert page.markdown == ""
    assert page.mime_type == "application/pdf"
    assert page.raw_bytes_size == len(pdf_bytes)


@respx.mock
def test_fetch_connection_error_raises_connection_error():
    respx.get("https://nowhere.example/").mock(side_effect=httpx.ConnectError("boom"))
    adapter = HttpxWebFetchAdapter()
    try:
        with pytest.raises(ProviderConnectionError):
            adapter.fetch("https://nowhere.example/")
    finally:
        adapter.close()


@respx.mock
@pytest.mark.asyncio
async def test_afetch_runs_same_as_sync():
    respx.get("https://example.com/page").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )
    adapter = HttpxWebFetchAdapter()
    try:
        page = await adapter.afetch("https://example.com/page")
    finally:
        await adapter.aclose()
    assert "Hello" in page.markdown
    assert page.images_total >= 1


def test_score_image_prefers_content_over_chrome():
    content_score = _score_image("https://example.com/photo.jpg", "a nice photograph of the subject")
    logo_score = _score_image("https://example.com/logo.svg", "logo")
    assert content_score > logo_score
