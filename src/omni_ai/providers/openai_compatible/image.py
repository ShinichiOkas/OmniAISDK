"""OpenAICompatibleImageAdapter — OpenAI 互換の ``POST {base_url}/images/generations``。

- ``ImageCapability`` の OpenAI 互換アダプター。接続先（``…/v1``）は必須。キーは任意で、会話と同じ
  ``OMNI_OPENAI_COMPATIBLE_API_KEY`` から読む
- 要求は OpenAI の標準: ``{"prompt", "n", "size", "model"?, "response_format": "b64_json"}``
- 応答は ``{"created": int, "data": [{"b64_json": "…"} | {"url": "…"}]}``。``url`` が来たら取りに行く
  （そのとき ``Authorization`` は付けない — 別のホストにキーを渡さない）
- **寸法の表を持たない。** ``size`` は呼び出し側が引数で渡す（モデルごとに使える寸法は違う）
- 環境変数での割り当ては ``OMNI_IMAGE_PROVIDER=openai-compatible`` と ``OMNI_IMAGE_BASE_URL``
  （:mod:`omni_ai.env_overlay`）
"""

from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import Any, ClassVar

import httpx

from ...capabilities import ImageCapability
from ...exceptions import InvalidRequestError, OmniError
from ...types import GeneratedImage, ImageResponse, ResponseMeta
from ._service import OpenAICompatibleServiceBase, provider_namespace_options

__all__ = ["OpenAICompatibleImageAdapter"]

logger = logging.getLogger("omni_ai")

# 画像生成は 1 分を超えることがある。読みは長く待ち、繋がらない相手は早めに諦める
IMAGE_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=10.0)

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)


def _sniff_mime(data: bytes, fallback: str = "image/png") -> str:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


class OpenAICompatibleImageAdapter(OpenAICompatibleServiceBase, ImageCapability):
    provider_name: ClassVar[str] = "openai-compatible"
    service: ClassVar[str] = "image generation"
    default_timeout: ClassVar[httpx.Timeout] = IMAGE_TIMEOUT

    def __init__(self, *, default_model: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.default_model = default_model

    # ------------------------------------------------------------------ 要求・応答

    def _build_body(
        self, prompt: str, model: str | None, size: str, n: int, provider_options: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        if not prompt or not prompt.strip():
            raise InvalidRequestError("prompt is empty", provider=self.provider_name)
        if n < 1:
            raise InvalidRequestError(f"n must be >= 1 (got {n})", provider=self.provider_name)
        resolved = model or self.default_model
        body: dict[str, Any] = {"prompt": prompt, "n": n, "size": size, "response_format": "b64_json"}
        if resolved:
            body["model"] = resolved
        body.update(provider_namespace_options(self.provider_name, provider_options, what="image"))
        return body, resolved

    def _parse(
        self, data: Any, model: str | None, started: float, attempts: int, request_id: str | None
    ) -> tuple[ImageResponse, list[tuple[int, str]]]:
        """応答を ImageResponse にする。``url`` で来た分は ``data`` が空で、(images の添字, URL) を順に返す。"""
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise OmniError(
                "image response has no data", provider=self.provider_name, model=model, raw_error=data
            )
        images: list[GeneratedImage] = []
        pending: list[tuple[int, str]] = []
        for item in items:
            if not isinstance(item, dict):
                logger.warning("%s image: skipped a non-object entry in data", self.provider_name)
                continue
            revised = item.get("revised_prompt")
            b64 = item.get("b64_json")
            if b64:
                try:
                    raw = base64.b64decode(b64)
                except (binascii.Error, ValueError) as exc:
                    raise OmniError(
                        f"image b64_json is not valid base64: {exc}",
                        provider=self.provider_name,
                        model=model,
                        raw_error=exc,
                    ) from exc
                if not raw:
                    raise OmniError(
                        "image b64_json decoded to 0 bytes", provider=self.provider_name, model=model, raw_error=data
                    )
                images.append(GeneratedImage(data=raw, mime_type=_sniff_mime(raw), revised_prompt=revised))
            elif item.get("url"):
                pending.append((len(images), str(item["url"])))
                images.append(GeneratedImage(data=b"", revised_prompt=revised))
            else:
                logger.warning("%s image: entry has neither b64_json nor url; skipped", self.provider_name)
        if not images:
            raise OmniError(
                "image response has no usable entry", provider=self.provider_name, model=model, raw_error=data
            )
        meta = ResponseMeta(
            provider=self.provider_name,
            model=model or "",
            latency_ms=(time.monotonic() - started) * 1000.0,
            attempts=attempts,
            request_id=request_id,
        )
        return ImageResponse(images=images, meta=meta), pending

    def _fill(
        self, res: ImageResponse, pending: list[tuple[int, str]], fetched: list[httpx.Response]
    ) -> ImageResponse:
        """url で来た画像に、取ってきたバイト列を入れる（添字で置くので順序は応答のまま）。"""
        for (idx, url), resp in zip(pending, fetched, strict=True):
            if not resp.content:
                raise OmniError(
                    f"image url returned an empty body: {url}", provider=self.provider_name, raw_error=url
                )
            img = res.images[idx]
            img.data = resp.content
            ctype = resp.headers.get("content-type", "").split(";")[0].strip()
            img.mime_type = ctype if ctype.startswith("image/") else _sniff_mime(resp.content)
            logger.debug("%s image: fetched %s (%d bytes)", self.provider_name, url, len(resp.content))
        return res

    # ------------------------------------------------------------------ 公開

    def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
        **provider_options: Any,
    ) -> ImageResponse:
        body, resolved = self._build_body(prompt, model, size, n, provider_options)
        started = time.monotonic()
        result = self._sync.request("POST", "/images/generations", json=body)
        res, pending = self._parse(
            self._json(result), resolved, started, result.attempts, result.response.headers.get("x-request-id")
        )
        fetched = [self._fetch(u) for _, u in pending]
        res.meta.latency_ms = (time.monotonic() - started) * 1000.0
        return self._fill(res, pending, fetched)

    async def agenerate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
        **provider_options: Any,
    ) -> ImageResponse:
        body, resolved = self._build_body(prompt, model, size, n, provider_options)
        started = time.monotonic()
        result = await self._async.request("POST", "/images/generations", json=body)
        res, pending = self._parse(
            self._json(result), resolved, started, result.attempts, result.response.headers.get("x-request-id")
        )
        fetched = [await self._afetch(u) for _, u in pending]
        res.meta.latency_ms = (time.monotonic() - started) * 1000.0
        return self._fill(res, pending, fetched)

    # ------------------------------------------------------------------ url の取得（キーを付けない）

    def _fetch(self, url: str) -> httpx.Response:
        try:
            resp = self._sync.client.get(url, timeout=self._sync.timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise self._sync._map_exception(exc) from exc
        if not resp.is_success:
            raise self._sync._error_from_response(resp, resp.content, self._map_error)
        return resp

    async def _afetch(self, url: str) -> httpx.Response:
        try:
            resp = await self._async.client.get(url, timeout=self._async.timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise self._async._map_exception(exc) from exc
        if not resp.is_success:
            raise self._async._error_from_response(resp, resp.content, self._map_error)
        return resp
