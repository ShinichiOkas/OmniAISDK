"""OllamaEmbeddingAdapter（ネイティブ /api/embed）。実装設計書 §14。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import RetryPolicy
from ...compat import PlatformCompat
from ...config import resolve_setting
from ...exceptions import ContextLengthExceededError, OmniError, UnsupportedFeatureError
from .._embed_base import BaseHttpEmbeddingAdapter, EmbedBatchResult, EmbedRequest
from . import OLLAMA_TIMEOUT, codec

__all__ = ["OllamaEmbeddingAdapter"]

logger = logging.getLogger("omni_ai")


class OllamaEmbeddingAdapter(BaseHttpEmbeddingAdapter):
    provider_name: ClassVar[str] = "ollama"
    max_batch_size = 256

    def __init__(
        self,
        *,
        default_model: str | None = None,
        base_url: str | None = None,
        truncate: bool = True,
        check_capabilities: bool = True,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        setting = resolve_setting("ollama", "base_url", base_url)
        if setting.value is None:
            url = PlatformCompat.resolve_ollama_base_url()
        elif setting.source == "provider_env":
            url = PlatformCompat.normalize_ollama_host(setting.value)
        else:
            url = setting.value.rstrip("/")
        super().__init__(
            base_url=url,
            default_model=default_model,
            retry=retry,
            timeout=timeout if timeout is not None else OLLAMA_TIMEOUT,
            http_client=http_client,
            async_http_client=async_http_client,
        )
        # 実測: 既定 (truncate=true) では文脈長を超えた入力を黙って切り詰める。false なら 400
        self.truncate = truncate
        self.check_capabilities = check_capabilities
        self._caps: dict[str, frozenset[str] | None] = {}

    def _endpoint(self, req: EmbedRequest, batch: list[str]) -> str:
        return "/api/embed"

    def _build_body(self, req: EmbedRequest, batch: list[str]) -> dict[str, Any]:
        if req.task:
            logger.debug(
                "ollama embedding: task=%s ignored (use model-specific prefixes if needed)", req.task
            )
        body: dict[str, Any] = {
            "model": req.model,
            "input": batch,
            "truncate": req.options.get("truncate", self.truncate),
        }
        if req.dimensions is not None:
            body["dimensions"] = req.dimensions
        if "keep_alive" in req.options:
            body["keep_alive"] = req.options["keep_alive"]
        return body

    def _parse_body(self, data: Any, req: EmbedRequest, batch: list[str]) -> EmbedBatchResult:
        return EmbedBatchResult(
            vectors=[list(map(float, v)) for v in data.get("embeddings") or []],
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            model=data.get("model"),
        )

    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError:
        err = codec.map_error(status, body, headers, model)
        if status == 400 and "exceeds the context length" in err.message:  # 実測 (truncate=false)
            return ContextLengthExceededError(err.message, provider="ollama", model=model, status_code=400)
        return err

    # 実測: チャット用モデルで /api/embed を呼ぶと 404「pull してください」になり紛らわしい → 事前に確認する
    def _check(self, req: EmbedRequest, caps: frozenset[str] | None) -> None:
        if caps is not None and "embedding" not in caps:
            raise UnsupportedFeatureError(
                f"model {req.model!r} is not an embedding model (capabilities: {sorted(caps)})",
                provider="ollama",
                model=req.model,
            )

    @staticmethod
    def _caps_of(data: Any) -> frozenset[str] | None:
        caps = data.get("capabilities") if isinstance(data, dict) else None
        return frozenset(caps) if isinstance(caps, list) else None

    def _preflight(self, req: EmbedRequest) -> None:
        if not self.check_capabilities:
            return
        if req.model not in self._caps:
            data = self._sync.request(
                "POST", "/api/show", json={"model": req.model}, error_mapper=self._mapper(req.model)
            ).json()
            self._caps[req.model] = self._caps_of(data)
        self._check(req, self._caps[req.model])

    async def _apreflight(self, req: EmbedRequest) -> None:
        if not self.check_capabilities:
            return
        if req.model not in self._caps:
            r = await self._async.request(
                "POST", "/api/show", json={"model": req.model}, error_mapper=self._mapper(req.model)
            )
            self._caps[req.model] = self._caps_of(r.json())
        self._check(req, self._caps[req.model])
