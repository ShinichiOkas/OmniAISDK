"""OpenAICompatibleSearchAdapter — 「OpenAI 互換の検索の口」の ``POST {base_url}/search``。

``SearchCapability`` の HTTP アダプター。前段のプロキシ（ellm-proxy など）が ``/v1/search`` で出す口に向く。
どの検索エンジンで引くかはプロキシ側の事情で、このアダプターは名前を知らない。

- 要求: ``{"query": str, "max_results": int, "backend"?: str, ...}``
  ``backend`` は省略可（既定は付けない＝プロキシの既定に任せる）。``"ellm"``／``"ddgs"`` のような
  プロキシが決めた名前をそのまま渡す。付けた名前は結果の ``meta.model`` に出る
- 応答: ``{"results": [{"title", "url", "snippet"}]}``（``snippet`` が無ければ ``content`` を読む）
- 失敗は例外で返す。**ほかの検索へ自動で切り替えない**（HTTP の状態は :mod:`.codec` の ``map_error`` に従う）
- 接続先（``…/v1``）は必須。キーは任意で、会話と同じ ``OMNI_OPENAI_COMPATIBLE_API_KEY`` から読む
- 環境変数での割り当ては ``OMNI_SEARCH_PROVIDER=openai-compatible`` と ``OMNI_SEARCH_BASE_URL``
  （:mod:`omni_ai.env_overlay`）
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar, Literal

import httpx

from ...capabilities import SearchCapability
from ...exceptions import InvalidRequestError, OmniError
from ...types import ResponseMeta, SearchItem, SearchResponse
from ._service import OpenAICompatibleServiceBase, provider_namespace_options

__all__ = ["OpenAICompatibleSearchAdapter"]

logger = logging.getLogger("omni_ai")

MAX_RESULTS_LIMIT = 50
SEARCH_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=30.0, pool=10.0)


class OpenAICompatibleSearchAdapter(OpenAICompatibleServiceBase, SearchCapability):
    provider_name: ClassVar[str] = "openai-compatible"
    service: ClassVar[str] = "search"
    default_timeout: ClassVar[httpx.Timeout] = SEARCH_TIMEOUT

    def __init__(self, *, backend: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.backend = backend or None

    # ------------------------------------------------------------------ 要求・応答

    def _build_body(
        self, query: str, max_results: int, search_depth: str, provider_options: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        if not query or not query.strip():
            raise InvalidRequestError("query is empty", provider=self.provider_name)
        if search_depth != "basic":
            logger.debug("%s search: search_depth=%s is not supported; ignored", self.provider_name, search_depth)
        limit = max(1, min(int(max_results), MAX_RESULTS_LIMIT))
        body: dict[str, Any] = {"query": query.strip(), "max_results": limit}
        if self.backend:
            body["backend"] = self.backend
        body.update(provider_namespace_options(self.provider_name, provider_options, what="search"))
        return body, limit

    def _parse(
        self, data: Any, body: dict[str, Any], limit: int, started: float, attempts: int, request_id: str | None
    ) -> SearchResponse:
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise OmniError(
                "search response has no 'results' list", provider=self.provider_name, raw_error=data
            )
        items: list[SearchItem] = []
        for r in rows:
            if not isinstance(r, dict):
                logger.warning("%s search: skipped a non-object entry in results", self.provider_name)
                continue
            url = str(r.get("url") or "")
            if not url:
                continue
            score = r.get("score")
            items.append(
                SearchItem(
                    title=str(r.get("title") or ""),
                    url=url,
                    content=str(r.get("snippet") or r.get("content") or ""),
                    score=float(score) if isinstance(score, (int, float)) else None,
                )
            )
        return SearchResponse(
            results=items[:limit],
            meta=ResponseMeta(
                provider=self.provider_name,
                model=str(body.get("backend") or ""),
                latency_ms=(time.monotonic() - started) * 1000.0,
                attempts=attempts,
                request_id=request_id,
            ),
        )

    # ------------------------------------------------------------------ 公開

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **provider_options: Any,
    ) -> SearchResponse:
        body, limit = self._build_body(query, max_results, search_depth, provider_options)
        started = time.monotonic()
        result = self._sync.request("POST", "/search", json=body)
        return self._parse(
            result.json(), body, limit, started, result.attempts, result.response.headers.get("x-request-id")
        )

    async def asearch(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **provider_options: Any,
    ) -> SearchResponse:
        body, limit = self._build_body(query, max_results, search_depth, provider_options)
        started = time.monotonic()
        result = await self._async.request("POST", "/search", json=body)
        return self._parse(
            result.json(), body, limit, started, result.attempts, result.response.headers.get("x-request-id")
        )
