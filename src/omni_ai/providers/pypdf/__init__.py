"""PyPDFDocumentParser — pypdf による PDF のテキスト抽出（extras ``[docs]``）。"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, ClassVar

from ..._media import rewind, tell_or_none
from .._doc_base import PDF_MIME, BaseLocalDocumentParser

__all__ = ["PyPDFDocumentParser"]

logger = logging.getLogger("omni_ai")


class PyPDFDocumentParser(BaseLocalDocumentParser):
    provider_name: ClassVar[str] = "pypdf"
    supported_mimes: ClassVar[frozenset[str]] = frozenset({PDF_MIME})

    def _reader(self, source: Any) -> Any:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - extras 未導入
            raise ImportError('PyPDFDocumentParser requires pypdf: pip install "omni-ai-sdk[docs]"') from exc
        if isinstance(source, bytes):
            return PdfReader(io.BytesIO(source))
        if isinstance(source, Path):
            return PdfReader(str(source))
        return PdfReader(source)

    def _extract(self, source: Any, mime: str) -> tuple[str, int | None, str | None]:
        start = tell_or_none(source) if not isinstance(source, (bytes, Path)) else None
        pages: int | None = None
        try:
            reader = self._reader(source)
            if reader.is_encrypted:
                try:
                    unlocked = bool(reader.decrypt(""))  # 空パスワードは自動では試されない
                except Exception:
                    unlocked = False
                if not unlocked:
                    try:
                        pages = len(reader.pages)
                    except Exception:
                        pages = None
                    return "", pages, "暗号化された PDF のため本文を取り出せない"
            pages = len(reader.pages)
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            logger.warning("pypdf: extraction failed: %s", exc)
            return "", pages, f"変換に失敗した: {exc}"
        finally:
            if start is not None:
                rewind(source, start)
        if not text.strip():
            return "", pages, "本文の文字が無い（画像だけの PDF の可能性。OCR は行っていない）"
        return text, pages, None
