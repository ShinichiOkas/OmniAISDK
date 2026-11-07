"""httpx の薄いラッパー（ADR-06）。同期・非同期の 2 本を、同じ再試行ロジックで動かす。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any

import anyio
import httpx

from .._version import __version__
from ..exceptions import OmniError, ProviderConnectionError, ProviderTimeoutError
from .retry import RetryPolicy

__all__ = [
    "AsyncTransport",
    "ErrorMapper",
    "HttpResult",
    "StreamHandle",
    "SyncTransport",
    "redact_headers",
]

ErrorMapper = Callable[[int, bytes, Mapping[str, str]], OmniError]

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0)
_SENSITIVE = ("key", "auth", "cookie", "token", "secret")


def redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """例外に添えるヘッダから、認証情報らしいものを取り除く。"""
    return {k: v for k, v in headers.items() if not any(s in k.lower() for s in _SENSITIVE)}


@dataclass
class HttpResult:
    response: httpx.Response
    attempts: int

    def json(self) -> Any:
        return self.response.json()


@dataclass
class StreamHandle:
    response: httpx.Response
    attempts: int


class _TransportBase:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        error_mapper: ErrorMapper,
        headers: Mapping[str, str] | None = None,
        timeout: httpx.Timeout | float | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.error_mapper = error_mapper
        self.headers = {"User-Agent": f"omni-ai-sdk/{__version__} ({provider})", **(headers or {})}
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self.retry = retry or RetryPolicy()
        self.closed = False

    def url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _check_open(self) -> None:
        if self.closed:
            raise OmniError("transport is closed", provider=self.provider)

    def _timeout(self, override: float | None) -> httpx.Timeout | float:
        return override if override is not None else self.timeout

    def _map_exception(self, exc: httpx.HTTPError) -> OmniError:
        if isinstance(exc, httpx.TimeoutException):
            return ProviderTimeoutError(f"request timed out: {exc}", provider=self.provider, raw_error=exc)
        return ProviderConnectionError(
            f"connection failed: {type(exc).__name__}: {exc}", provider=self.provider, raw_error=exc
        )

    def _error_from_response(self, response: httpx.Response, body: bytes, mapper: ErrorMapper) -> OmniError:
        err = mapper(response.status_code, body, response.headers)
        if err.status_code is None:
            err.status_code = response.status_code
        if err.raw_error is None:
            err.raw_error = {
                "status": response.status_code,
                "body": body[:2048].decode("utf-8", errors="replace"),
                "headers": redact_headers(response.headers),
            }
        return err


class SyncTransport(_TransportBase):
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep

    @property
    def client(self) -> httpx.Client:
        self._check_open()
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _send_with_retry(
        self,
        method: str,
        path: str,
        *,
        stream: bool,
        timeout: float | None,
        error_mapper: ErrorMapper | None,
        retry: RetryPolicy | None,
        headers: Mapping[str, str] | None,
        **kwargs: Any,
    ) -> tuple[httpx.Response, int]:
        policy = retry or self.retry
        mapper = error_mapper or self.error_mapper
        attempt = 0
        while True:
            attempt += 1
            request = self.client.build_request(
                method,
                self.url(path),
                headers={**self.headers, **(headers or {})},
                timeout=self._timeout(timeout),
                **kwargs,
            )
            try:
                response = self.client.send(request, stream=stream)
            except httpx.HTTPError as exc:
                err = self._map_exception(exc)
            else:
                if response.is_success:
                    return response, attempt
                body = response.read()
                response.close()
                err = self._error_from_response(response, body, mapper)
            delay = policy.next_delay(attempt, err)
            if delay is None:
                raise err
            self._sleep(delay)

    def request(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        error_mapper: ErrorMapper | None = None,
        retry: RetryPolicy | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> HttpResult:
        response, attempts = self._send_with_retry(
            method,
            path,
            stream=False,
            timeout=timeout,
            error_mapper=error_mapper,
            retry=retry,
            headers=headers,
            **kwargs,
        )
        return HttpResult(response, attempts)

    @contextmanager
    def stream(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        error_mapper: ErrorMapper | None = None,
        retry: RetryPolicy | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> Iterator[StreamHandle]:
        """2xx を受け取るまで再試行し、本体のバイト列を読む手前で呼び出し側に渡す。"""
        response, attempts = self._send_with_retry(
            method,
            path,
            stream=True,
            timeout=timeout,
            error_mapper=error_mapper,
            retry=retry,
            headers=headers,
            **kwargs,
        )
        try:
            yield StreamHandle(response, attempts)
        finally:
            response.close()

    def iter_bytes(self, handle: StreamHandle) -> Iterator[bytes]:
        try:
            yield from handle.response.iter_bytes()
        except httpx.HTTPError as exc:
            raise self._map_exception(exc) from exc

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
        self._client = None
        self.closed = True


class AsyncTransport(_TransportBase):
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Any] = anyio.sleep,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._owns_client = client is None
        self._sleep = sleep

    @property
    def client(self) -> httpx.AsyncClient:
        self._check_open()
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _send_with_retry(
        self,
        method: str,
        path: str,
        *,
        stream: bool,
        timeout: float | None,
        error_mapper: ErrorMapper | None,
        retry: RetryPolicy | None,
        headers: Mapping[str, str] | None,
        **kwargs: Any,
    ) -> tuple[httpx.Response, int]:
        policy = retry or self.retry
        mapper = error_mapper or self.error_mapper
        attempt = 0
        while True:
            attempt += 1
            request = self.client.build_request(
                method,
                self.url(path),
                headers={**self.headers, **(headers or {})},
                timeout=self._timeout(timeout),
                **kwargs,
            )
            try:
                response = await self.client.send(request, stream=stream)
            except httpx.HTTPError as exc:
                err = self._map_exception(exc)
            else:
                if response.is_success:
                    return response, attempt
                body = await response.aread()
                await response.aclose()
                err = self._error_from_response(response, body, mapper)
            delay = policy.next_delay(attempt, err)
            if delay is None:
                raise err
            await self._sleep(delay)

    async def request(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        error_mapper: ErrorMapper | None = None,
        retry: RetryPolicy | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> HttpResult:
        response, attempts = await self._send_with_retry(
            method,
            path,
            stream=False,
            timeout=timeout,
            error_mapper=error_mapper,
            retry=retry,
            headers=headers,
            **kwargs,
        )
        return HttpResult(response, attempts)

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        path: str,
        *,
        timeout: float | None = None,
        error_mapper: ErrorMapper | None = None,
        retry: RetryPolicy | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamHandle]:
        response, attempts = await self._send_with_retry(
            method,
            path,
            stream=True,
            timeout=timeout,
            error_mapper=error_mapper,
            retry=retry,
            headers=headers,
            **kwargs,
        )
        try:
            yield StreamHandle(response, attempts)
        finally:
            await response.aclose()

    async def aiter_bytes(self, handle: StreamHandle) -> AsyncIterator[bytes]:
        try:
            async for chunk in handle.response.aiter_bytes():
                yield chunk
        except httpx.HTTPError as exc:
            raise self._map_exception(exc) from exc

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None
        self.closed = True
