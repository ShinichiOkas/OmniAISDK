"""MarkItDownDocumentParser — markitdown による Office / RTF の Markdown 化（extras ``[docs]``）。

⚠ markitdown の ``convert()`` に文字列を渡すと URL として取りに行くことがある。
ここでは Path かストリームだけを渡す。
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any, ClassVar

from ..._media import rewind, tell_or_none
from .._doc_base import DOCUMENT_EXTENSIONS, PDF_MIME, BaseLocalDocumentParser, extension_for

__all__ = ["MarkItDownDocumentParser"]

logger = logging.getLogger("omni_ai")

OFFICE_MIMES = frozenset(m for m in DOCUMENT_EXTENSIONS.values() if m != PDF_MIME)


class MarkItDownDocumentParser(BaseLocalDocumentParser):
    provider_name: ClassVar[str] = "markitdown"
    supported_mimes: ClassVar[frozenset[str]] = OFFICE_MIMES

    def __init__(self) -> None:
        self._md: Any = None

    def _engine(self) -> Any:
        if self._md is None:
            try:
                from markitdown import MarkItDown
            except ImportError as exc:  # pragma: no cover - extras 未導入
                raise ImportError(
                    'MarkItDownDocumentParser requires markitdown: pip install "omni-ai-sdk[docs]"'
                ) from exc
            self._md = MarkItDown()
        return self._md

    def _extract(self, source: Any, mime: str) -> tuple[str, int | None, str | None]:
        md = self._engine()
        start = tell_or_none(source) if not isinstance(source, (bytes, Path)) else None
        try:
            if isinstance(source, Path):
                result = md.convert(source)  # Path を渡す（str は URL 扱いになりうる）
            else:
                from markitdown import StreamInfo

                stream = io.BytesIO(source) if isinstance(source, bytes) else source
                result = md.convert_stream(
                    stream, stream_info=StreamInfo(mimetype=mime, extension=extension_for(mime))
                )
            text = result.text_content or ""
        except Exception as exc:
            logger.warning("markitdown: conversion failed: %s", exc)
            return "", None, f"変換に失敗した: {exc}"
        finally:
            if start is not None:
                rewind(source, start)
        return text, None, None
