"""文書解析の共通部分（実装設計書 §17）。"""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import anyio.to_thread

from .._text import normalize_cjk_forms
from ..capabilities import DocumentParserCapability
from ..exceptions import InvalidRequestError, UnsupportedFeatureError
from ..types import FileRef, ParsedDocument, ResponseMeta, UrlRef

__all__ = [
    "DOCUMENT_EXTENSIONS",
    "BaseLocalDocumentParser",
    "MimeRoutingDocumentParser",
    "guess_document_mime",
]

logger = logging.getLogger("omni_ai")

PDF_MIME = "application/pdf"
# 拡張子 → MIME。`mimetypes` は OS のレジストリ次第で .docx を知らないことがあるため、明示の表を持つ
DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".pdf": PDF_MIME,
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".rtf": "application/rtf",
}
_EXT_BY_MIME = {v: k for k, v in reversed(DOCUMENT_EXTENSIONS.items())}


def guess_document_mime(source: Any) -> str | None:
    """Path（またはファイルオブジェクトの name）の拡張子から MIME を引く。中身は見ない。"""
    name = source if isinstance(source, Path) else getattr(source, "name", None)
    if isinstance(name, (str, Path)):
        return DOCUMENT_EXTENSIONS.get(Path(name).suffix.lower())
    return None


def extension_for(mime: str) -> str:
    return _EXT_BY_MIME.get(mime, "")


class BaseLocalDocumentParser(DocumentParserCapability):
    """ローカルのライブラリで文書を読むアダプターの骨格。

    - 入力は Path / bytes / ストリーム。``UrlRef`` / ``FileRef`` は受け付けない（勝手にダウンロードしない）
    - 中身が取り出せなかったとき（暗号化・画像だけの PDF・変換失敗）は例外にせず、
      ``empty_reason`` に理由を入れて返す
    - 本文は康熙部首・互換漢字を通常の漢字へ写す（``normalize_cjk_forms``）
    """

    supported_mimes: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def _extract(self, source: Any, mime: str) -> tuple[str, int | None, str | None]:
        """(本文, ページ数, 取り出せなかった理由) を返す。"""

    def _resolve_mime(self, source: Any, mime_type: str | None) -> str:
        if isinstance(source, (UrlRef, FileRef)):
            raise UnsupportedFeatureError(
                f"{self.provider_name} parses local data only (Path / bytes / stream)",
                provider=self.provider_name,
            )
        if isinstance(source, str):
            raise InvalidRequestError(
                "pass pathlib.Path(...) for files (str is ambiguous)", provider=self.provider_name
            )
        mime = (mime_type or guess_document_mime(source) or "").lower()
        if not mime:
            raise InvalidRequestError(
                "mime_type is required for bytes / streams without a name", provider=self.provider_name
            )
        if mime not in self.supported_mimes:
            raise UnsupportedFeatureError(
                f"{self.provider_name} does not parse {mime}", provider=self.provider_name
            )
        return mime

    def parse(
        self, file_source: Any, *, mime_type: str | None = None, **provider_options: Any
    ) -> ParsedDocument:
        mime = self._resolve_mime(file_source, mime_type)
        text, pages, reason = self._extract(file_source, mime)
        if reason is None and not text.strip():
            reason = "本文が空だった"
        fixed = normalize_cjk_forms(text) if text else text
        if fixed != text:
            logger.info(
                "%s: normalized %d CJK radical/compatibility glyph(s)",
                self.provider_name,
                sum(1 for a, b in zip(text, fixed, strict=True) if a != b),
            )
        return ParsedDocument(
            markdown="" if reason else fixed,
            page_count=pages,
            mime_type=mime,
            empty_reason=reason,
            metadata={"engine": self.provider_name},
            meta=ResponseMeta(provider=self.provider_name, model=self.provider_name, estimated_cost_usd=0.0),
        )

    async def aparse(
        self, file_source: Any, *, mime_type: str | None = None, **provider_options: Any
    ) -> ParsedDocument:
        return await anyio.to_thread.run_sync(lambda: self.parse(file_source, mime_type=mime_type))


class MimeRoutingDocumentParser(DocumentParserCapability):
    """MIME によって担当のアダプターへ振り分ける（例: PDF は pypdf、Office は markitdown）。

    結果の ``meta.provider`` は、実際に処理したエンジンの名前になる。
    """

    provider_name: ClassVar[str] = "mime-router"

    def __init__(self, parsers: Sequence[DocumentParserCapability]) -> None:
        if not parsers:
            raise ValueError("At least one parser is required.")
        self.parsers = list(parsers)

    def _pick(self, source: Any, mime_type: str | None) -> tuple[DocumentParserCapability, str | None]:
        mime = (mime_type or guess_document_mime(source) or "").lower() or None
        for p in self.parsers:
            if mime and mime in getattr(p, "supported_mimes", frozenset()):
                return p, mime
        raise UnsupportedFeatureError(f"no parser for {mime or 'unknown type'}", provider=self.provider_name)

    def parse(
        self, file_source: Any, *, mime_type: str | None = None, **provider_options: Any
    ) -> ParsedDocument:
        parser, mime = self._pick(file_source, mime_type)
        return parser.parse(file_source, mime_type=mime, **provider_options)

    async def aparse(
        self, file_source: Any, *, mime_type: str | None = None, **provider_options: Any
    ) -> ParsedDocument:
        parser, mime = self._pick(file_source, mime_type)
        return await parser.aparse(file_source, mime_type=mime, **provider_options)

    @property
    def supported_mimes(self) -> frozenset[str]:
        out: frozenset[str] = frozenset()
        for p in self.parsers:
            out |= getattr(p, "supported_mimes", frozenset())
        return out

    def close(self) -> None:
        for p in self.parsers:
            p.close()

    async def aclose(self) -> None:
        for p in self.parsers:
            await p.aclose()
