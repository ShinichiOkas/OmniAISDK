"""Gemini Files API（resumable アップロード）。実装設計書 §7.7。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import anyio
import anyio.to_thread

from ..._http import NO_RETRY, AsyncTransport, SyncTransport
from ..._media import display_name, media_size, rewind, tell_or_none
from ...exceptions import InvalidRequestError, ProviderTimeoutError
from ...types import FileRef

__all__ = ["aupload_file", "upload_file"]

PROVIDER = "gemini"


def upload_url(base_url: str) -> str:
    """``https://host/v1beta`` → ``https://host/upload/v1beta/files``。"""
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, "/upload" + parts.path.rstrip("/") + "/files", "", ""))


def _start_headers(size: int, mime_type: str) -> dict[str, str]:
    return {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(size),
        "X-Goog-Upload-Header-Content-Type": mime_type,
    }


_FINALIZE = {"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"}


def _size(source: Any) -> int:
    size = media_size(source)
    if size is None:
        raise InvalidRequestError(
            "upload requires a sized source (bytes, Path or a seekable stream)", provider=PROVIDER
        )
    return size


def _to_ref(data: Mapping[str, Any], mime_type: str) -> tuple[FileRef, str]:
    f = data.get("file", data)
    ref = FileRef(
        provider=PROVIDER, file_id=f["name"], uri=f.get("uri"), mime_type=f.get("mimeType", mime_type)
    )
    return ref, str(f.get("state", "ACTIVE"))


def _check_state(state: str, ref: FileRef) -> bool:
    if state == "FAILED":
        raise InvalidRequestError(f"Gemini file processing failed: {ref.file_id}", provider=PROVIDER)
    return state == "ACTIVE"


_CHUNK = 1024 * 1024


async def _achunks(source: Any) -> AsyncIterator[bytes]:
    """全体をメモリに載せずに流す（Path は anyio のファイル、ストリームはスレッドで読む）。"""
    if isinstance(source, Path):
        async with await anyio.open_file(source, "rb") as f:
            while chunk := await f.read(_CHUNK):
                yield chunk
        return
    while chunk := await anyio.to_thread.run_sync(source.read, _CHUNK):
        yield chunk


def upload_file(t: SyncTransport, source: Any, mime_type: str, *, wait_timeout: float = 120.0) -> FileRef:
    size = _size(source)
    start = t.request(
        "POST",
        upload_url(t.base_url),
        headers=_start_headers(size, mime_type),
        json={"file": {"display_name": display_name(source)}},
        retry=NO_RETRY,
    )
    session = start.response.headers.get("x-goog-upload-url")
    if not session:
        raise InvalidRequestError("Gemini upload did not return x-goog-upload-url", provider=PROVIDER)
    start_pos = tell_or_none(source) if not isinstance(source, (bytes, Path)) else None
    # Content-Length は「現在位置から末尾まで」を明示する（httpx の推定はストリーム全体の長さになる）
    headers = {**_FINALIZE, "Content-Length": str(size)}
    try:
        with ExitStack() as stack:
            content = stack.enter_context(source.open("rb")) if isinstance(source, Path) else source
            done = t.request("POST", session, headers=headers, content=content, retry=NO_RETRY)
    finally:
        if not isinstance(source, (bytes, Path)):
            rewind(source, start_pos)
    ref, state = _to_ref(done.json(), mime_type)
    deadline = time.monotonic() + wait_timeout
    delay = 0.5
    while not _check_state(state, ref):
        if time.monotonic() > deadline:
            raise ProviderTimeoutError(f"file {ref.file_id} not ACTIVE in {wait_timeout}s", provider=PROVIDER)
        t._sleep(delay)
        delay = min(delay * 2, 5.0)
        ref, state = _to_ref(t.request("GET", f"/{ref.file_id}").json(), mime_type)
    return ref


async def aupload_file(
    t: AsyncTransport, source: Any, mime_type: str, *, wait_timeout: float = 120.0
) -> FileRef:
    size = _size(source)
    start = await t.request(
        "POST",
        upload_url(t.base_url),
        headers=_start_headers(size, mime_type),
        json={"file": {"display_name": display_name(source)}},
        retry=NO_RETRY,
    )
    session = start.response.headers.get("x-goog-upload-url")
    if not session:
        raise InvalidRequestError("Gemini upload did not return x-goog-upload-url", provider=PROVIDER)
    start_pos = tell_or_none(source) if not isinstance(source, (bytes, Path)) else None
    content: Any = source if isinstance(source, bytes) else _achunks(source)
    headers = {**_FINALIZE, "Content-Length": str(size)}
    try:
        done = await t.request("POST", session, headers=headers, content=content, retry=NO_RETRY)
    finally:
        if not isinstance(source, (bytes, Path)):
            rewind(source, start_pos)
    ref, state = _to_ref(done.json(), mime_type)
    deadline = time.monotonic() + wait_timeout
    delay = 0.5
    while not _check_state(state, ref):
        if time.monotonic() > deadline:
            raise ProviderTimeoutError(f"file {ref.file_id} not ACTIVE in {wait_timeout}s", provider=PROVIDER)
        await t._sleep(delay)
        delay = min(delay * 2, 5.0)
        ref, state = _to_ref((await t.request("GET", f"/{ref.file_id}")).json(), mime_type)
    return ref
