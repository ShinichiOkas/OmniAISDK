"""GeminiEmbeddingAdapter（embedContent / batchEmbedContents）。実装設計書 §14。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from ..._http import RetryPolicy
from ...config import resolve_setting
from ...exceptions import AuthenticationError, OmniError
from .._embed_base import BaseHttpEmbeddingAdapter, EmbedBatchResult, EmbedRequest
from . import codec
from .codec import model_path

__all__ = ["DEFAULT_EMBEDDING_MODEL", "GeminiEmbeddingAdapter"]

logger = logging.getLogger("omni_ai")

# 実測 (2026-11-07): gemini-embedding-2 は usageMetadata を返す（-001 は返さない）。既定 3072 次元
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"
_TASK = {"query": "RETRIEVAL_QUERY", "document": "RETRIEVAL_DOCUMENT"}


class GeminiEmbeddingAdapter(BaseHttpEmbeddingAdapter):
    provider_name: ClassVar[str] = "gemini"
    # [未検証] batchEmbedContents の 1 要求あたりの上限。保守的に 100 で分割する
    max_batch_size = 100

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = DEFAULT_EMBEDDING_MODEL,
        base_url: str | None = None,
        retry: RetryPolicy | None = None,
        timeout: httpx.Timeout | float | None = None,
        http_client: httpx.Client | None = None,
        async_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        key = resolve_setting("gemini", "api_key", api_key)
        if not key.value:
            raise AuthenticationError("Gemini API key not found", provider=self.provider_name)
        from . import DEFAULT_BASE_URL

        url = (
            resolve_setting("gemini", "base_url", base_url, default=DEFAULT_BASE_URL).value
            or DEFAULT_BASE_URL
        )
        super().__init__(
            base_url=url,
            headers={"x-goog-api-key": key.value},
            default_model=default_model,
            retry=retry,
            timeout=timeout,
            http_client=http_client,
            async_http_client=async_http_client,
        )

    def _endpoint(self, req: EmbedRequest, batch: list[str]) -> str:
        # 実測: batchEmbedContents はモデル一覧の対応メソッドに載っていないが動く
        return f"/{model_path(req.model)}:batchEmbedContents"

    def _one(self, req: EmbedRequest, text: str) -> dict[str, Any]:
        item: dict[str, Any] = {"model": model_path(req.model), "content": {"parts": [{"text": text}]}}
        if req.dimensions is not None:
            item["outputDimensionality"] = req.dimensions
        task = req.options.get("task_type") or (_TASK.get(req.task) if req.task else None)
        if task:
            item["taskType"] = task
        if "title" in req.options:
            item["title"] = req.options["title"]
        return item

    def _build_body(self, req: EmbedRequest, batch: list[str]) -> dict[str, Any]:
        return {"requests": [self._one(req, t) for t in batch]}

    def _parse_body(self, data: Any, req: EmbedRequest, batch: list[str]) -> EmbedBatchResult:
        embeddings = data.get("embeddings") or []
        usage = data.get("usageMetadata") or {}
        return EmbedBatchResult(
            vectors=[list(map(float, e.get("values") or [])) for e in embeddings],
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
        )

    def _map_error(
        self, status: int, body: bytes, headers: Mapping[str, str], model: str | None
    ) -> OmniError:
        return codec.map_error(status, body, headers, model)
