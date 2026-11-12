"""OpenAI 互換の「会話以外の口」（画像・検索）のアダプターが共有する土台。

接続先は必須（既定の URL を持たない。推測すると別のサーバーに繋がる）。キーは任意で、
会話と同じ ``OMNI_OPENAI_COMPATIBLE_API_KEY``（``resolve_setting`` の層）から読む。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import AsyncTransport, RetryPolicy, SyncTransport
from ...config import resolve_setting
from ...exceptions import InvalidRequestError, OmniError
from . import codec, normalize_base_url

__all__ = ["OpenAICompatibleServiceBase", "provider_namespace_options"]

logger = logging.getLogger("omni_ai")


def provider_namespace_options(
    provider: str, provider_options: Mapping[str, Any], *, what: str
) -> dict[str, Any]:
    """``provider_options[<provider>]`` の dict だけを採る。他の名前空間は捨てるが、黙って捨てない。"""
    ours: dict[str, Any] = {}
    for key, value in provider_options.items():
        if key == provider and isinstance(value, dict):
            ours = dict(value)
        else:
            logger.debug("%s %s adapter ignored provider option %r", provider, what, key)
    return ours


class OpenAICompatibleServiceBase:
    provider_name: ClassVar[str] = "openai-compatible"
    # 何の口か（エラー文言用）。サブクラスが差し替える
    service: ClassVar[str] = "service"
    default_timeout: ClassVar[httpx.Timeout] = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=10.0)

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        name = self.provider_name
        url = normalize_base_url(base_url or "")
        if not url:
            raise InvalidRequestError(
                f"{name} {self.service} needs base_url (e.g. http://127.0.0.1:8080/v1)", provider=name
            )
        key = resolve_setting(name, "api_key", api_key).value
        headers: dict[str, str] = dict(extra_headers or {})
        if key:
            headers["Authorization"] = f"Bearer {key}"
        common: dict[str, Any] = {
            "provider": name,
            "base_url": url,
            "headers": headers,
            "timeout": timeout if timeout is not None else self.default_timeout,
            "retry": retry,
            "error_mapper": self._map_error,
        }
        self._sync = SyncTransport(client=http_client, **common)
        self._async = AsyncTransport(client=async_http_client, **common)

    @property
    def base_url(self) -> str:
        return self._sync.base_url

    def _json(self, result: Any) -> Any:
        """200 なのに JSON でない応答（接続先の取り違えで HTML が返るなど）を OmniError にする。"""
        try:
            return result.json()
        except ValueError as exc:
            raise OmniError(
                f"{self.provider_name} {self.service} returned a non-JSON body "
                f"(check base_url={self.base_url})",
                provider=self.provider_name,
                raw_error=exc,
            ) from exc

    def _map_error(self, status: int, body: bytes, headers: Mapping[str, str]) -> OmniError:
        return codec.map_error(status, body, headers, None, provider=self.provider_name)

    def close(self) -> None:
        self._sync.close()
        self._async.mark_closed_sync()

    async def aclose(self) -> None:
        await self._async.aclose()
        self._sync.close()
