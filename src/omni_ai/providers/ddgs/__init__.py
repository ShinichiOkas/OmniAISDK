"""DDGSSearchAdapter — ddgs（複数の検索エンジンを横断するライブラリ）による Web 検索。

- API キー不要。既定は backend="auto"（bing・brave・duckduckgo・google 等を横断）
- 既定の region="ja-jp"、safesearch="on"
- 依存は extras（``pip install "omni-ai-sdk[search]"``）
- 公式 API ではなく検索結果を読み取るライブラリなので、レート制限・仕様変更で止まりうる
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, ClassVar, Literal

import anyio.to_thread

from ..._http import RetryPolicy
from ...capabilities import SearchCapability
from ...exceptions import (
    InvalidRequestError,
    OmniError,
    ProviderOverloadedError,
    ProviderTimeoutError,
    RateLimitError,
)
from ...types import ResponseMeta, SearchItem, SearchResponse

__all__ = ["DDGSSearchAdapter"]

logger = logging.getLogger("omni_ai")

MAX_RESULTS_LIMIT = 50


def _default_factory(**kwargs: Any) -> Any:
    try:
        from ddgs import DDGS
    except ImportError as exc:  # pragma: no cover - extras 未導入
        raise ImportError('DDGSSearchAdapter requires ddgs: pip install "omni-ai-sdk[search]"') from exc
    return DDGS(**kwargs)


def _map_exception(exc: Exception) -> OmniError | None:
    """ddgs の例外を OmniError に写像する。「結果 0 件」は None（＝空の結果）。"""
    name = type(exc).__name__
    msg = str(exc) or name
    if name == "RatelimitException":
        return RateLimitError(msg, provider="ddgs", raw_error=exc)
    if name == "TimeoutException":
        return ProviderTimeoutError(msg, provider="ddgs", raw_error=exc)
    if name == "DDGSException":
        if "no results" in msg.lower():  # 実測: 結果 0 件は DDGSException("No results found.")
            return None
        return ProviderOverloadedError(msg, provider="ddgs", raw_error=exc)
    return ProviderOverloadedError(f"{name}: {msg}", provider="ddgs", raw_error=exc)


class DDGSSearchAdapter(SearchCapability):
    provider_name: ClassVar[str] = "ddgs"

    def __init__(
        self,
        *,
        region: str = "ja-jp",
        safesearch: Literal["on", "moderate", "off"] = "on",
        backend: str = "auto",
        timeout: int = 10,
        proxy: str | None = None,
        retry: RetryPolicy | None = None,
        ddgs_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.region = region
        self.safesearch = safesearch
        self.backend = backend
        self.timeout = timeout
        self.proxy = proxy
        self.retry = retry or RetryPolicy(max_attempts=2, base_delay=2.0)
        self._factory = ddgs_factory or _default_factory
        self._sleep = sleep
        self._closed = False

    def _options(self, provider_options: dict[str, Any]) -> dict[str, Any]:
        ours: dict[str, Any] = {}
        for key, value in provider_options.items():
            if key == self.provider_name and isinstance(value, dict):
                ours = dict(value)
            else:
                logger.debug("ddgs search adapter ignored provider option %r", key)
        return ours

    def _search_once(self, query: str, max_results: int, opts: dict[str, Any]) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        with self._factory(**kwargs) as client:
            result: list[dict[str, Any]] = client.text(
                query,
                region=opts.get("region", self.region),
                safesearch=opts.get("safesearch", self.safesearch),
                timelimit=opts.get("timelimit"),
                backend=opts.get("backend", self.backend),
                max_results=max_results,
            )
            return result

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **provider_options: Any,
    ) -> SearchResponse:
        if self._closed:
            raise OmniError("adapter is closed", provider=self.provider_name)
        if not query or not query.strip():
            raise InvalidRequestError("query is empty", provider=self.provider_name)
        if search_depth != "basic":
            logger.debug("ddgs: search_depth=%s is not supported; ignored", search_depth)
        limit = max(1, min(int(max_results), MAX_RESULTS_LIMIT))
        opts = self._options(provider_options)
        attempt = 0
        while True:
            attempt += 1
            try:
                raw = self._search_once(query, limit, opts)
                break
            except Exception as exc:
                err = _map_exception(exc)
                if err is None:
                    raw = []
                    break
                delay = self.retry.next_delay(attempt, err)
                if delay is None:
                    raise err from exc
                self._sleep(delay)
        items: list[SearchItem] = []
        seen: set[str] = set()
        for r in raw:
            url = str(r.get("href") or r.get("url") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            items.append(
                SearchItem(title=str(r.get("title") or ""), url=url, content=str(r.get("body") or ""))
            )
        return SearchResponse(
            results=items[:limit],
            meta=ResponseMeta(
                provider=self.provider_name,
                model=opts.get("backend", self.backend),
                attempts=attempt,
                estimated_cost_usd=0.0,
            ),
        )

    async def asearch(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **provider_options: Any,
    ) -> SearchResponse:
        # ddgs は同期ライブラリ（内部でスレッドを使う）→ イベントループを止めないようスレッドで実行
        return await anyio.to_thread.run_sync(
            lambda: self.search(query, max_results=max_results, search_depth=search_depth, **provider_options)
        )

    def close(self) -> None:
        self._closed = True

    async def aclose(self) -> None:
        self._closed = True
