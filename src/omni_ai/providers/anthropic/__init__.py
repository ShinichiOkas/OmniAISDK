"""Anthropic（Claude）Messages API の Chat アダプター。

公式 SDK には依存せず、他のアダプターと同じ ``_http``（httpx）で ``POST /v1/messages`` を直接叩く。
既定のモデルを持たない（モデル名の入れ替わりが速いため、設定か引数で指定する）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import RetryPolicy
from ...config import resolve_setting
from ...exceptions import AuthenticationError, OmniError
from ...types import BuiltinTool, ChatResponse
from .._base import BaseChatAdapter, ChatRequest, StreamState
from . import codec

__all__ = ["ANTHROPIC_VERSION", "DEFAULT_BASE_URL", "AnthropicChatAdapter"]

DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# 思考の長いモデルが黙り続けても待つ。繋がらない相手は早めに諦める
ANTHROPIC_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=10.0)


class AnthropicChatAdapter(BaseChatAdapter):
    provider_name: ClassVar[str] = "anthropic"
    # サーバーツール（web_search など）は未対応。必要になったら別の票で足す
    supported_builtins: frozenset[BuiltinTool] = frozenset()

    def __init__(
        self,
        *,
        default_model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        default_max_tokens: int = codec.DEFAULT_MAX_TOKENS,
        extra_headers: Mapping[str, str] | None = None,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        key = resolve_setting("anthropic", "api_key", api_key).value
        if not key:
            raise AuthenticationError(
                "anthropic API key not found "
                "(api_key=, OMNI_ANTHROPIC_API_KEY, ANTHROPIC_API_KEY "
                "or [anthropic] api_key in secrets.toml)",
                provider="anthropic",
            )
        url = resolve_setting("anthropic", "base_url", base_url, default=DEFAULT_BASE_URL).value
        headers: dict[str, str] = dict(extra_headers or {})
        headers["x-api-key"] = key
        headers.setdefault("anthropic-version", ANTHROPIC_VERSION)
        super().__init__(
            base_url=(url or DEFAULT_BASE_URL).rstrip("/"),
            headers=headers,
            default_model=default_model,
            retry=retry,
            timeout=timeout if timeout is not None else ANTHROPIC_TIMEOUT,
            http_client=http_client,
            async_http_client=async_http_client,
        )
        self.default_max_tokens = default_max_tokens

    @property
    def base_url(self) -> str:
        return self._sync.base_url

    # ------------------------------------------------------------------ codec フック

    def _endpoint(self, req: ChatRequest, *, stream: bool) -> str:
        return "/v1/messages"

    def _build_body(self, req: ChatRequest, *, stream: bool) -> dict[str, Any]:
        return codec.build_body(req, stream=stream, default_max_tokens=self.default_max_tokens)

    def _parse_body(self, data: Any, req: ChatRequest) -> ChatResponse:
        return codec.parse_response(data, req)

    def _new_stream_state(self, req: ChatRequest) -> StreamState:
        return codec.AnthropicStreamState(req)

    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError:
        return codec.map_error(status, body, headers, model)
