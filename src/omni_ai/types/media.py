"""マルチモーダル入力型。"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator

__all__ = ["ContentPart", "FileRef", "MediaPart", "MediaSource", "TextPart", "UrlRef", "is_binary_stream"]


class FileRef(BaseModel):
    """プロバイダ側に事前アップロードされたファイル参照。"""

    provider: str
    file_id: str
    uri: str | None = None
    mime_type: str


class UrlRef(BaseModel):
    """URL で指定するメディア。SDK は自動ダウンロードしない。"""

    url: str


MediaSource = Union[bytes, Path, BinaryIO, FileRef, UrlRef]  # noqa: UP007  (BinaryIO を含むため typing.Union)


def is_binary_stream(value: Any) -> bool:
    return hasattr(value, "read") and not isinstance(value, (str, bytes, bytearray))


def _validate_media_source(value: Any) -> Any:
    # 素の str は「パス／URL／base64」のどれか曖昧なので受け付けない（仕様書 v3.1 D-03）
    if isinstance(value, str):
        raise ValueError(
            "str is ambiguous as a media source; use pathlib.Path(...) for files or UrlRef(url=...) for URLs"
        )
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, (bytes, Path, FileRef, UrlRef)):
        return value
    if isinstance(value, dict):
        if "url" in value:
            return UrlRef.model_validate(value)
        if "file_id" in value:
            return FileRef.model_validate(value)
    if is_binary_stream(value):
        return value
    raise ValueError(f"unsupported media source type: {type(value).__name__}")


def _serialize_media_source(value: Any) -> Any:
    if isinstance(value, (FileRef, UrlRef)):
        return value.model_dump()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return f"<stream {type(value).__name__}>"


_MediaSourceField = Annotated[
    Any,
    PlainValidator(_validate_media_source),
    PlainSerializer(_serialize_media_source, return_type=Any),
]


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class MediaPart(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: Literal["image", "audio", "video", "document"]
    source: _MediaSourceField
    mime_type: str


ContentPart = Annotated[Union[TextPart, MediaPart], Field(discriminator="type")]  # noqa: UP007
