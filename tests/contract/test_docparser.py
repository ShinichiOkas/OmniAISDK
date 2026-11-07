"""文書解析の契約テスト。tests/fixtures/docs の実物のファイル（make_fixtures.py で生成）を使う。"""

import io
from pathlib import Path

import pytest

from omni_ai import InvalidRequestError, OmniClient, UnsupportedFeatureError, UrlRef
from omni_ai._text import normalize_cjk_forms
from omni_ai.providers import default_document_parser
from omni_ai.providers._doc_base import guess_document_mime
from omni_ai.providers.markitdown import MarkItDownDocumentParser
from omni_ai.providers.pypdf import PyPDFDocumentParser

pytest.importorskip("pypdf")
pytest.importorskip("markitdown")

DOCS = Path(__file__).parent.parent / "fixtures" / "docs"


def test_pdf_text_pages_and_meta() -> None:
    d = PyPDFDocumentParser().parse(DOCS / "text.pdf")
    assert d.markdown == "OmniAI SDK page one\n\nSecond page about cats"
    assert d.page_count == 2 and d.empty_reason is None and d.mime_type == "application/pdf"
    assert d.meta.provider == "pypdf" and d.meta.estimated_cost_usd == 0.0


# 実測 (pypdf 7.0): 暗号化された PDF は復号しないとページ数も読めない → page_count は None
@pytest.mark.parametrize(
    ("name", "reason", "pages"), [("encrypted.pdf", "暗号化", None), ("image_only.pdf", "画像だけ", 1)]
)
def test_pdf_empty_reasons(name: str, reason: str, pages: int | None) -> None:
    d = PyPDFDocumentParser().parse(DOCS / name)
    assert d.markdown == "" and reason in (d.empty_reason or "") and d.page_count == pages


def test_pdf_bytes_and_stream_rewind() -> None:
    data = (DOCS / "text.pdf").read_bytes()
    assert "page one" in PyPDFDocumentParser().parse(data, mime_type="application/pdf").markdown
    stream = io.BytesIO(data)
    stream.seek(0)
    p = PyPDFDocumentParser()
    assert p.parse(stream, mime_type="application/pdf").page_count == 2
    assert stream.tell() == 0  # 位置を戻す → 同じストリームを何度でも渡せる
    assert p.parse(stream, mime_type="application/pdf").page_count == 2


@pytest.mark.parametrize(
    ("name", "expect"),
    [
        ("memo.docx", "窓口の受付は9時から"),
        ("table.xlsx", "| りんご | 3 |"),
        ("slides.pptx", "猫は1日に12〜16時間眠る"),
    ],
)
def test_markitdown_office(name: str, expect: str) -> None:
    d = MarkItDownDocumentParser().parse(DOCS / name)
    assert expect in d.markdown and d.meta.provider == "markitdown" and d.empty_reason is None


def test_markitdown_bytes_needs_mime() -> None:
    data = (DOCS / "memo.docx").read_bytes()
    p = MarkItDownDocumentParser()
    with pytest.raises(InvalidRequestError, match="mime_type"):
        p.parse(data)
    assert "窓口" in p.parse(data, mime_type=guess_document_mime(DOCS / "memo.docx")).markdown


def test_router_dispatches_by_extension_and_reports_engine() -> None:
    r = default_document_parser()
    assert r.parse(DOCS / "text.pdf").meta.provider == "pypdf"
    assert r.parse(DOCS / "slides.pptx").meta.provider == "markitdown"
    with pytest.raises(UnsupportedFeatureError):
        r.parse(Path("notes.txt"))


def test_rejects_urls_strs_and_unsupported() -> None:
    p = PyPDFDocumentParser()
    with pytest.raises(UnsupportedFeatureError):
        p.parse(UrlRef(url="https://example.com/a.pdf"))
    with pytest.raises(InvalidRequestError):
        p.parse(str(DOCS / "text.pdf"))
    with pytest.raises(UnsupportedFeatureError):
        p.parse(DOCS / "memo.docx")


def test_cjk_normalization_only_targets_radicals() -> None:
    s = "窓⼝・⾴・ＡＢＣ①ｶﾅ"
    assert normalize_cjk_forms(s) == "窓口・頁・ＡＢＣ①ｶﾅ"


def test_cjk_normalization_applied_to_parsed_text(monkeypatch: pytest.MonkeyPatch) -> None:
    p = PyPDFDocumentParser()
    monkeypatch.setattr(p, "_extract", lambda source, mime: ("窓⼝で受付", 1, None))
    assert p.parse(DOCS / "text.pdf").markdown == "窓口で受付"


async def test_async_and_client_telemetry() -> None:
    ai = OmniClient(doc_parser=default_document_parser())
    d = await ai.doc_parser.aparse(DOCS / "table.xlsx")
    assert "みかん" in d.markdown
    assert ai.telemetry.history[0].provider == "markitdown" and ai.telemetry.unpriced_calls == 0
    await ai.aclose()
