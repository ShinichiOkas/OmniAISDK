"""OllamaChatAdapter（ネイティブ /api/chat）。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import RetryPolicy
from ...compat import PlatformCompat
from ...config import resolve_setting
from ...exceptions import OmniError, ProviderConnectionError, UnsupportedFeatureError
from ...types import BuiltinTool, ChatResponse
from .._base import BaseChatAdapter, ChatRequest, StreamState
from . import codec

__all__ = ["OllamaChatAdapter"]

logger = logging.getLogger("omni_ai")

# モデルのロードに時間がかかるため read を長めにする（実装設計書 §5.7）
OLLAMA_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)


class OllamaChatAdapter(BaseChatAdapter):
    provider_name: ClassVar[str] = "ollama"
    supported_builtins: ClassVar[frozenset[BuiltinTool]] = frozenset()

    def __init__(
        self,
        *,
        default_model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        check_capabilities: bool = True,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        url_setting = resolve_setting("ollama", "base_url", base_url)
        if url_setting.value is None:
            resolved_url = PlatformCompat.resolve_ollama_base_url()
        elif url_setting.source == "provider_env":  # OLLAMA_HOST は Ollama 本体と同じ規則で読む
            resolved_url = PlatformCompat.normalize_ollama_host(url_setting.value)
        else:
            resolved_url = url_setting.value.rstrip("/")
        headers: dict[str, str] = {}
        if api_key:  # リモートの Ollama をリバースプロキシ越しに使う場合など
            headers["Authorization"] = f"Bearer {api_key}"
        super().__init__(
            base_url=resolved_url,
            headers=headers,
            default_model=default_model,
            retry=retry,
            timeout=timeout if timeout is not None else OLLAMA_TIMEOUT,
            http_client=http_client,
            async_http_client=async_http_client,
        )
        self.check_capabilities = check_capabilities
        self._capabilities: dict[str, frozenset[str] | None] = {}
        self._container_fallback_tried = False

    @property
    def base_url(self) -> str:
        return self._sync.base_url

    # ------------------------------------------------------------------ codec フック

    def _endpoint(self, req: ChatRequest, *, stream: bool) -> str:
        return "/api/chat"

    def _build_body(self, req: ChatRequest, *, stream: bool) -> dict[str, Any]:
        return codec.build_chat_body(req, stream=stream)

    def _parse_body(self, data: Any, req: ChatRequest) -> ChatResponse:
        return codec.parse_chat_response(data, req)

    def _new_stream_state(self, req: ChatRequest) -> StreamState:
        return codec.OllamaStreamState(req)

    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError:
        return codec.map_error(status, body, headers, model)

    # ------------------------------------------------------------------ 能力確認 (/api/show)

    def _check(self, req: ChatRequest, caps: frozenset[str] | None) -> None:
        if caps is None:
            return  # capabilities を返さない古い Ollama では確認しない
        missing = codec.required_capabilities(req) - caps
        if missing:
            raise UnsupportedFeatureError(
                f"model {req.model!r} lacks capabilities {sorted(missing)} (has {sorted(caps)})",
                provider=self.provider_name,
                model=req.model,
            )

    @staticmethod
    def _caps_from_show(data: Any) -> frozenset[str] | None:
        caps = data.get("capabilities") if isinstance(data, dict) else None
        return frozenset(caps) if isinstance(caps, list) else None

    def _switch_to_container_host(self, exc: ProviderConnectionError) -> bool:
        if self._container_fallback_tried:
            return False
        self._container_fallback_tried = True
        fallback = PlatformCompat.container_host_fallback_url()
        if fallback is None or self.base_url == fallback:
            return False
        logger.warning("Ollama unreachable at %s (%s); retrying via %s", self.base_url, exc, fallback)
        self._sync.base_url = fallback
        self._async.base_url = fallback
        return True

    def _preflight(self, req: ChatRequest) -> None:
        if not self.check_capabilities or not codec.required_capabilities(req):
            return
        if req.model not in self._capabilities:
            try:
                data = self._sync.request(
                    "POST",
                    "/api/show",
                    json={"model": req.model},
                    error_mapper=self._error_mapper_for(req.model),
                ).json()
            except ProviderConnectionError as exc:
                if not self._switch_to_container_host(exc):
                    raise
                data = self._sync.request(
                    "POST",
                    "/api/show",
                    json={"model": req.model},
                    error_mapper=self._error_mapper_for(req.model),
                ).json()
            self._capabilities[req.model] = self._caps_from_show(data)
        self._check(req, self._capabilities[req.model])

    async def _apreflight(self, req: ChatRequest) -> None:
        if not self.check_capabilities or not codec.required_capabilities(req):
            return
        if req.model not in self._capabilities:
            try:
                result = await self._async.request(
                    "POST",
                    "/api/show",
                    json={"model": req.model},
                    error_mapper=self._error_mapper_for(req.model),
                )
            except ProviderConnectionError as exc:
                if not self._switch_to_container_host(exc):
                    raise
                result = await self._async.request(
                    "POST",
                    "/api/show",
                    json={"model": req.model},
                    error_mapper=self._error_mapper_for(req.model),
                )
            self._capabilities[req.model] = self._caps_from_show(result.json())
        self._check(req, self._capabilities[req.model])
