"""GeminiChatAdapter（Gemini Developer API の generateContent / v1beta）。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import RetryPolicy
from ..._media import b64_size, media_size
from ...config import resolve_setting
from ...exceptions import AuthenticationError, OmniError
from ...types import BuiltinTool, ChatResponse, FileRef, MediaPart, UrlRef
from .._base import BaseChatAdapter, ChatRequest, StreamState
from . import codec
from .files import aupload_file, upload_file

__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "GeminiChatAdapter"]

logger = logging.getLogger("omni_ai")

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiChatAdapter(BaseChatAdapter):
    provider_name: ClassVar[str] = "gemini"
    supported_builtins: frozenset[BuiltinTool] = frozenset(
        {BuiltinTool.WEB_SEARCH, BuiltinTool.CODE_EXECUTION}
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        inline_limit: int = codec.DEFAULT_INLINE_LIMIT,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        key = resolve_setting("gemini", "api_key", api_key)
        if not key.value:
            raise AuthenticationError(
                "Gemini API key not found (api_key=, OMNI_GEMINI_API_KEY, GEMINI_API_KEY, GOOGLE_API_KEY "
                "or [gemini] api_key in config.toml)",
                provider=self.provider_name,
            )
        url = (
            resolve_setting("gemini", "base_url", base_url, default=DEFAULT_BASE_URL).value
            or DEFAULT_BASE_URL
        )
        super().__init__(
            base_url=url,
            # キーは URL に入れずヘッダで送る（ログに残りにくい）
            headers={"x-goog-api-key": key.value},
            default_model=default_model,
            retry=retry,
            timeout=timeout,
            http_client=http_client,
            async_http_client=async_http_client,
        )
        self.inline_limit = inline_limit

    # ------------------------------------------------------------------ codec フック

    def _endpoint(self, req: ChatRequest, *, stream: bool) -> str:
        return codec.endpoint(req, stream=stream)

    def _build_body(self, req: ChatRequest, *, stream: bool) -> dict[str, Any]:
        req.options.setdefault("inline_limit", self.inline_limit)
        if req.temperature is not None:
            logger.debug("gemini: temperature may be ignored by Gemini 3.6+ models")
        return codec.build_body(req, stream=stream)

    def _parse_body(self, data: Any, req: ChatRequest) -> ChatResponse:
        return codec.parse_response(data, req)

    def _new_stream_state(self, req: ChatRequest) -> StreamState:
        return codec.GeminiStreamState(req)

    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError:
        return codec.map_error(status, body, headers, model)

    # ------------------------------------------------------------------ 大きなメディアは Files API へ

    def _oversized(self, req: ChatRequest) -> list[tuple[int, int, MediaPart]]:
        found = []
        for mi, msg in enumerate(req.messages):
            if isinstance(msg.content, str):
                continue
            for pi, part in enumerate(msg.content):
                if isinstance(part, MediaPart) and not isinstance(part.source, (FileRef, UrlRef)):
                    size = media_size(part.source)
                    if size is not None and b64_size(size) > self.inline_limit:
                        found.append((mi, pi, part))
        return found

    @staticmethod
    def _replace(req: ChatRequest, mi: int, pi: int, part: MediaPart, ref: FileRef) -> None:
        msg = req.messages[mi]
        assert not isinstance(msg.content, str)
        parts = list(msg.content)
        parts[pi] = MediaPart(type=part.type, source=ref, mime_type=part.mime_type)
        req.messages[mi] = msg.model_copy(update={"content": parts})

    def _preflight(self, req: ChatRequest) -> None:
        for mi, pi, part in self._oversized(req):
            self._replace(req, mi, pi, part, upload_file(self._sync, part.source, part.mime_type))

    async def _apreflight(self, req: ChatRequest) -> None:
        for mi, pi, part in self._oversized(req):
            self._replace(req, mi, pi, part, await aupload_file(self._async, part.source, part.mime_type))

    # ------------------------------------------------------------------ 公開ユーティリティ

    def upload_file(self, source: Any, mime_type: str) -> FileRef:
        """事前アップロード（48 時間保持）。返り値は MediaPart.source に使える。"""
        return upload_file(self._sync, source, mime_type)

    async def aupload_file(self, source: Any, mime_type: str) -> FileRef:
        return await aupload_file(self._async, source, mime_type)

    @staticmethod
    def foreign_history_note() -> str:
        return (
            "assistant messages without Gemini provider_state are sent with the official dummy "
            f"thought signature {codec.DUMMY_THOUGHT_SIGNATURE!r}"
        )
