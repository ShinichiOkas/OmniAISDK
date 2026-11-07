"""MediaSource の解決（実装設計書 §5.8）。メモリに載せる量に上限を設ける。"""

from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path
from typing import Any

from .exceptions import UnsupportedFeatureError
from .types.media import FileRef, UrlRef, is_binary_stream

__all__ = ["b64_size", "load_bytes", "media_size", "rewind", "tell_or_none", "to_base64"]

logger = logging.getLogger("omni_ai")

_CHUNK = 1024 * 1024


def b64_size(n: int) -> int:
    """n バイトを base64 にしたときのバイト数。"""
    return 4 * ((n + 2) // 3)


def media_size(source: Any) -> int | None:
    """読み込まずに分かるならサイズを返す。分からなければ None。"""
    if isinstance(source, bytes):
        return len(source)
    if isinstance(source, Path):
        return source.stat().st_size
    if is_binary_stream(source):
        try:
            pos = source.tell()
            end = source.seek(0, io.SEEK_END)
            source.seek(pos, io.SEEK_SET)
            return int(end - pos)
        except (OSError, AttributeError, ValueError):
            return None
    return None


def tell_or_none(source: Any) -> int | None:
    try:
        pos = source.tell()
        return int(pos) if source.seekable() else None
    except (OSError, AttributeError, ValueError):
        return None


def rewind(source: Any, start: int | None) -> None:
    """読み終えたストリームを元の位置に戻す。戻せないストリームは 1 回しか送れないので警告する。"""
    if start is not None:
        source.seek(start)
    else:
        logger.warning("non-seekable media stream was consumed; it cannot be sent again (use bytes or Path)")


def load_bytes(source: Any, *, limit: int, provider: str) -> bytes:
    """bytes / Path / ストリームを、``limit`` バイトを超えない範囲で読み込む。"""
    if isinstance(source, (FileRef, UrlRef)):
        raise TypeError("FileRef / UrlRef cannot be loaded as bytes")
    size = media_size(source)
    if size is not None and size > limit:
        raise UnsupportedFeatureError(
            f"media is {size} bytes, exceeding the inline limit of {limit} bytes", provider=provider
        )
    if isinstance(source, bytes):
        return source
    if isinstance(source, Path):
        return source.read_bytes()
    if is_binary_stream(source):
        # 同じ履歴を何度も送る（ToolRunner の各ターン・フォールバック）ので、読んだら元の位置に戻す
        start = tell_or_none(source)
        buf = bytearray()
        try:
            while True:
                chunk = source.read(_CHUNK)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > limit:
                    raise UnsupportedFeatureError(
                        f"media stream exceeds the inline limit of {limit} bytes", provider=provider
                    )
        finally:
            rewind(source, start)
        return bytes(buf)
    raise TypeError(f"unsupported media source: {type(source).__name__}")


def to_base64(source: Any, *, limit: int, provider: str) -> str:
    """base64 にした後のサイズで上限を判定して返す。"""
    raw_limit = (limit // 4) * 3
    return base64.b64encode(load_bytes(source, limit=raw_limit, provider=provider)).decode("ascii")


def display_name(source: Any) -> str:
    if isinstance(source, Path):
        return source.name
    name = getattr(source, "name", None)
    if isinstance(name, str):
        return os.path.basename(name)  # noqa: PTH119 - ファイルオブジェクトの name は文字列
    return "upload"
