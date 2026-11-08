"""OpenAI 互換 /chat/completions の Chat アダプター。実装設計書 §25。

- ``OpenAICompatibleChatAdapter``: 手元で立てた OpenAI 互換サーバー（llama.cpp の llama-server など）。
  接続先は必須（既定の URL を持たない。推測すると別のサーバーに繋がる）、キーは任意
- ``XAIChatAdapter`` / ``AgnesChatAdapter`` / ``OpenRouterChatAdapter``: 同じ形を話すクラウド。
  違うのは接続先・キーの環境変数・構造化出力の方式だけ

どれも既定のモデルを持たない（モデル名の入れ替わりが速いため、設定か引数で指定する）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import RetryPolicy
from ...config import resolve_setting
from ...exceptions import AuthenticationError, InvalidRequestError, OmniError
from ...types import BuiltinTool, ChatResponse
from .._base import BaseChatAdapter, ChatRequest, StreamState
from . import codec
from .codec import ResponseFormatMode

__all__ = [
    "AgnesChatAdapter",
    "OpenAICompatibleChatAdapter",
    "OpenRouterChatAdapter",
    "XAIChatAdapter",
    "normalize_base_url",
]

# 思考の長いモデルが黙り続けても待つ。繋がらない相手は早めに諦める
OPENAI_COMPATIBLE_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=10.0)


def normalize_base_url(url: str) -> str:
    """手入力の URL を ``.../v1`` の形に揃える（``/v1`` が付いていれば足さない）。"""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    return url if url.endswith("/v1") else url + "/v1"


class OpenAICompatibleChatAdapter(BaseChatAdapter):
    provider_name: ClassVar[str] = "openai-compatible"
    supported_builtins: frozenset[BuiltinTool] = frozenset()

    # サブクラスが差し替える
    default_base_url: ClassVar[str | None] = None
    key_required: ClassVar[bool] = False
    default_response_format: ClassVar[ResponseFormatMode] = "json_schema"
    key_hint: ClassVar[str] = "OMNI_OPENAI_COMPATIBLE_API_KEY or [openai-compatible] api_key in secrets.toml"

    def __init__(
        self,
        *,
        default_model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        response_format: ResponseFormatMode | None = None,
        stream_usage: bool = True,
        extra_headers: Mapping[str, str] | None = None,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        name = self.provider_name
        url = resolve_setting(name, "base_url", base_url, default=self.default_base_url).value
        if not url:
            raise InvalidRequestError(
                f"{name} needs base_url (e.g. http://127.0.0.1:8080), "
                f"via base_url=, OMNI_{_env(name)}_BASE_URL or [{name}] base_url in config.toml",
                provider=name,
            )
        key = resolve_setting(name, "api_key", api_key).value
        if self.key_required and not key:
            raise AuthenticationError(f"{name} API key not found ({self.key_hint})", provider=name)
        headers: dict[str, str] = dict(extra_headers or {})
        if key:
            headers["Authorization"] = f"Bearer {key}"
        super().__init__(
            base_url=self._normalize(url),
            headers=headers,
            default_model=default_model,
            retry=retry,
            timeout=timeout if timeout is not None else OPENAI_COMPATIBLE_TIMEOUT,
            http_client=http_client,
            async_http_client=async_http_client,
        )
        self.response_format: ResponseFormatMode = response_format or self.default_response_format
        self.stream_usage = stream_usage

    @staticmethod
    def _normalize(url: str) -> str:
        return normalize_base_url(url)

    @property
    def base_url(self) -> str:
        return self._sync.base_url

    # ------------------------------------------------------------------ codec フック

    def _endpoint(self, req: ChatRequest, *, stream: bool) -> str:
        return "/chat/completions"

    def _build_body(self, req: ChatRequest, *, stream: bool) -> dict[str, Any]:
        return codec.build_chat_body(
            req,
            stream=stream,
            provider=self.provider_name,
            response_format=self.response_format,
            stream_usage=self.stream_usage,
        )

    def _parse_body(self, data: Any, req: ChatRequest) -> ChatResponse:
        return codec.parse_chat_response(data, req, provider=self.provider_name)

    def _new_stream_state(self, req: ChatRequest) -> StreamState:
        return codec.OpenAICompatibleStreamState(req, provider=self.provider_name)

    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError:
        return codec.map_error(status, body, headers, model, provider=self.provider_name)


def _env(name: str) -> str:
    return name.upper().replace("-", "_")


class XAIChatAdapter(OpenAICompatibleChatAdapter):
    """xAI（Grok）。"""

    provider_name: ClassVar[str] = "xai"
    default_base_url: ClassVar[str | None] = "https://api.x.ai/v1"
    key_required: ClassVar[bool] = True
    key_hint: ClassVar[str] = "api_key=, OMNI_XAI_API_KEY, XAI_API_KEY or [xai] api_key in secrets.toml"

    @staticmethod
    def _normalize(url: str) -> str:
        return url.rstrip("/")


class AgnesChatAdapter(OpenAICompatibleChatAdapter):
    """Agnes AI。構造化出力は json_object だけが確認済みなので、既定でそちらを使う。"""

    provider_name: ClassVar[str] = "agnes"
    default_base_url: ClassVar[str | None] = "https://apihub.agnes-ai.com/v1"
    key_required: ClassVar[bool] = True
    default_response_format: ClassVar[ResponseFormatMode] = "json_object"
    key_hint: ClassVar[str] = "api_key=, OMNI_AGNES_API_KEY, AGNES_API_KEY or [agnes] api_key in secrets.toml"

    @staticmethod
    def _normalize(url: str) -> str:
        return url.rstrip("/")


class OpenRouterChatAdapter(OpenAICompatibleChatAdapter):
    """OpenRouter。モデルは ``openai/gpt-4o-mini`` のような ``<提供元>/<モデル>`` の形。

    帰属ヘッダー（HTTP-Referer / X-Title）は付けない（付けると公開ランキングに集計される）。
    必要なら ``extra_headers=`` で渡す。
    """

    provider_name: ClassVar[str] = "openrouter"
    default_base_url: ClassVar[str | None] = "https://openrouter.ai/api/v1"
    key_required: ClassVar[bool] = True
    key_hint: ClassVar[str] = (
        "api_key=, OMNI_OPENROUTER_API_KEY, OPENROUTER_API_KEY or [openrouter] api_key in secrets.toml"
    )

    @staticmethod
    def _normalize(url: str) -> str:
        return url.rstrip("/")
