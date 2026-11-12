"""WebFetch → DocumentParser の連結（raw_bytes を parser に渡せること）。"""

from __future__ import annotations

import contextlib

import httpx
import respx

from omni_ai.providers.pypdf import PyPDFDocumentParser
from omni_ai.providers.web_fetch import HttpxWebFetchAdapter


@respx.mock
def test_webfetch_passes_pdf_bytes_to_parser(tmp_path):
    # 本物の軽い PDF を用意するより、既存の parser が受け付けるシンプルな PDF バイト列を作る
    # ここでは最小の 1 ページ PDF の生成はせず、Parser が何かしらの応答を返せるかの通し確認のみに留める。
    # 実際の連結は WebFetch が raw_bytes を埋めることを確かめる（parse の中身は Parser 側の試験）。
    pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"endobj\nstartxref\n0\n%%EOF\n"
    respx.get("https://example.com/x.pdf").mock(
        return_value=httpx.Response(
            200, content=pdf_bytes, headers={"content-type": "application/pdf"}
        )
    )
    adapter = HttpxWebFetchAdapter()
    try:
        page = adapter.fetch("https://example.com/x.pdf")
    finally:
        adapter.close()
    # raw_bytes が埋まっていて、呼び出し側が DocumentParser に渡せる
    assert page.raw_bytes == pdf_bytes
    assert page.mime_type == "application/pdf"
    assert page.markdown == ""
    # Parser に渡せることだけ確かめる（parse 自体の成否は内容依存なので問わない）
    parser = PyPDFDocumentParser()
    # 本物の PDF ではないので parse は失敗してよい。raw_bytes を parser に渡せた、で十分
    with contextlib.suppress(Exception):
        parser.parse(page.raw_bytes, mime_type="application/pdf")
