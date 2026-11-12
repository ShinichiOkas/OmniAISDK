"""HttpxWebFetchAdapter — httpx を使った Web 取得（extras ``[web]``）。

- 本文抽出: readability-lxml ＋ markdownify（既定）または trafilatura（任意）
- 画像: ``<img>`` の src/srcset と「原寸画像を指す ``<a href>``」を拾い、UI 装飾を点数で下げる
- リダイレクトの各段で ``url_filter`` を呼ぶ（SSRF 対策の差し込み口）
- 文書系（PDF・docx 等）は本文抽出せず、生バイトを ``raw_bytes`` に残し、
  呼び出し側で DocumentParser に回す想定

**実運用の実測**（参考のみ・ここは SDK の 1 本目の実装）:

- 広告・ナビ・script を除いた上で Markdown にする
- 画像の `_CHROME_TOKENS` に当たるファイル名パターン（``logo``・``icon``・``sprite`` など）は点数を下げる
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from collections.abc import Callable
from typing import Any, ClassVar, Literal
from urllib.parse import urljoin, urlparse

import httpx

from ...capabilities import WebFetchCapability
from ...exceptions import OmniError, ProviderConnectionError
from ...types import FetchedImage, FetchedPage, ResponseMeta


class ProviderError(OmniError):
    """プロバイダー固有の失敗（OmniError の既定で十分な場面で使う）。"""

__all__ = ["HttpxWebFetchAdapter"]

logger = logging.getLogger("omni_ai")

UrlFilter = Callable[[str], str | None]
"""URL を受け、止める理由（文字列）か ``None`` を返す。"""

# UI 装飾のファイル名に出てきやすい部分文字列（本文画像との並べ替えに使う）
_CHROME_TOKENS = frozenset(
    {
        "logo",
        "icon",
        "sprite",
        "avatar",
        "favicon",
        "button",
        "banner",
        "pixel",
        "beacon",
        "spacer",
        "arrow",
        "nav",
    }
)

_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tiff", ".tif")

_IMG_SRC_ATTRS = ("src", "data-src", "data-original", "data-lazy", "data-image")

# 文書 MIME（本文抽出をしない。呼び出し側が DocumentParser に回す）
_DOCUMENT_MIMES = frozenset(
    {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/rtf",
    }
)

_DEFAULT_USER_AGENT = "omni-ai-sdk/0.1 (+https://github.com/anthropics/omni-ai-sdk)"
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 1 ページあたりの素材上限


def _score_image(url: str, alt: str) -> int:
    """コンテンツ画像らしさの点数。高いほど本文画像、低いほど UI 装飾。"""
    score = 0
    lowered = url.lower()
    path = urlparse(lowered).path
    if alt and len(alt) > 8:
        score += 2
    elif alt:
        score += 1
    if path.endswith((".gif", ".svg", ".ico")):
        score -= 3
    if any(token in lowered for token in _CHROME_TOKENS):
        score -= 3
    return score


def _pick_from_srcset(srcset: str) -> str:
    """srcset から一番大きい候補を選ぶ。"""
    best_url = ""
    best_w = -1
    for candidate in srcset.split(","):
        parts = candidate.strip().split()
        if not parts:
            continue
        url = parts[0]
        width = 0
        for p in parts[1:]:
            if p.endswith("w"):
                with contextlib.suppress(ValueError):
                    width = int(p[:-1])
        if width > best_w:
            best_w = width
            best_url = url
    return best_url


def _extract_images(html: str, base_url: str, limit: int) -> tuple[list[FetchedImage], int]:
    """HTML から画像を抽出し、点数の高い順に並べて先頭 ``limit`` 件を返す。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return [], 0

    soup = BeautifulSoup(html, "html.parser")
    images: list[tuple[int, FetchedImage]] = []
    seen: set[str] = set()

    def add(raw: str | None, alt: str) -> None:
        if not raw:
            return
        raw = raw.strip()
        if not raw or raw.startswith(("data:", "javascript:", "#")):
            return
        absolute = urljoin(base_url, raw)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return
        if absolute in seen:
            return
        seen.add(absolute)
        score = _score_image(absolute, alt)
        images.append((score, FetchedImage(url=absolute, alt=alt or None, score=float(score))))

    for img in soup.find_all("img"):
        if img.attrs is None:
            continue
        alt = (img.get("alt") or img.get("title") or "").strip()
        chosen = ""
        for attr in _IMG_SRC_ATTRS:
            value = img.get(attr)
            if value:
                chosen = value
                break
        if not chosen:
            for attr in ("srcset", "data-srcset"):
                value = img.get(attr)
                if value:
                    chosen = _pick_from_srcset(value)
                    break
        add(chosen, alt)

    for anchor in soup.find_all("a"):
        if anchor.attrs is None:
            continue
        href = anchor.get("href")
        if not href:
            continue
        path = urlparse(urljoin(base_url, href)).path.lower()
        if any(path.endswith(ext) for ext in _IMAGE_EXTENSIONS):
            label = (anchor.get("title") or anchor.get_text(" ", strip=True) or "").strip()
            add(href, label)

    total = len(images)
    images.sort(key=lambda t: -t[0])
    return [im for _, im in images[:limit]], total


def _extract_links(html: str, base_url: str, limit: int = 100) -> list[str]:
    """HTML からリンク（http/https のみ）を 1 覧で返す。重複を除く。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.find_all("a"):
        if a.attrs is None:
            continue
        href = a.get("href")
        if not isinstance(href, str):
            continue
        absolute = urljoin(base_url, href.strip())
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
        if len(out) >= limit:
            break
    return out


def _extract_title(html: str) -> str:
    """HTML から title を抽出する（readability の short_title より素朴に）。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("title")
    if t is None or not t.string:
        h1 = soup.find("h1")
        return h1.get_text(strip=True) if h1 else ""
    return t.string.strip()


def _html_to_markdown_readability(html: str, base_url: str) -> tuple[str, str]:
    """readability + markdownify で本文 Markdown を作る。"""
    try:
        from markdownify import markdownify
        from readability import Document
    except ImportError as exc:
        raise ProviderError(
            f"extras [web] is not installed ({exc.name}). "
            "Install with: pip install 'omni-ai-sdk[web]'",
            provider="web_fetch",
        ) from exc
    doc = Document(html)
    title = doc.short_title() or _extract_title(html)
    summary_html = doc.summary(html_partial=True)
    md = markdownify(summary_html, heading_style="ATX")
    return title, md


def _html_to_markdown_trafilatura(html: str, base_url: str) -> tuple[str, str]:
    """trafilatura で本文 Markdown を作る（任意の抽出器）。"""
    try:
        import trafilatura
    except ImportError as exc:
        raise ProviderError(
            f"extras [web] with trafilatura is not installed ({exc.name}). "
            "Install with: pip install 'omni-ai-sdk[web]' trafilatura",
            provider="web_fetch",
        ) from exc
    title = _extract_title(html)
    md = trafilatura.extract(html, output_format="markdown", with_metadata=False) or ""
    return title, md


def _slice(text: str, offset: int, max_length: int) -> tuple[str, bool]:
    """``text`` を ``offset`` から ``max_length`` 字だけ返す。切ったら True。"""
    if offset <= 0 and len(text) <= max_length:
        return text, False
    start = max(0, offset)
    end = start + max_length
    out = text[start:end]
    return out, end < len(text) or start > 0


class HttpxWebFetchAdapter(WebFetchCapability):
    """httpx を使った既定の実装。1 インスタンスで複数の URL を順に取れる（内部でクライアントを使い回す）。

    - 同期経路（``fetch``）は内部で ``httpx.Client`` を立てる
    - 非同期経路（``afetch``）は ``httpx.AsyncClient``。長命にしたければ外側で ``close()`` / ``aclose()`` する
    """

    provider_name: ClassVar[str] = "httpx"

    def __init__(
        self,
        *,
        user_agent: str = _DEFAULT_USER_AGENT,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        url_filter: UrlFilter | None = None,
        follow_redirects: bool = True,
        max_redirects: int = 10,
        image_download_concurrency: int = 4,
        image_total_bytes: int = 20 * 1024 * 1024,
        image_per_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.url_filter = url_filter
        self.follow_redirects = follow_redirects
        self.max_redirects = max_redirects
        self.image_download_concurrency = image_download_concurrency
        self.image_total_bytes = image_total_bytes
        self.image_per_bytes = image_per_bytes
        self._client: httpx.Client | None = None
        self._aclient: httpx.AsyncClient | None = None

    # ---------- クライアント管理 ----------

    def _sync_client(self, timeout: float) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"User-Agent": self.user_agent},
                follow_redirects=False,  # 自前で回す（url_filter を各段で呼ぶ）
                timeout=timeout,
            )
        return self._client

    def _async_client(self, timeout: float) -> httpx.AsyncClient:
        if self._aclient is None:
            self._aclient = httpx.AsyncClient(
                headers={"User-Agent": self.user_agent},
                follow_redirects=False,
                timeout=timeout,
            )
        return self._aclient

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def aclose(self) -> None:
        if self._aclient is not None:
            await self._aclient.aclose()
            self._aclient = None

    # ---------- URL チェック ----------

    def _check(self, url: str) -> None:
        if self.url_filter is None:
            return
        reason = self.url_filter(url)
        if reason is not None:
            raise ProviderError(f"url_filter refused {url}: {reason}", provider=self.provider_name)

    # ---------- リダイレクトを自前で回す ----------

    def _resolve_sync(
        self, client: httpx.Client, url: str
    ) -> httpx.Response:
        self._check(url)
        current = url
        for _ in range(self.max_redirects + 1):
            try:
                resp = client.get(current)
            except httpx.ConnectError as exc:
                raise ProviderConnectionError(f"connect failed: {exc}", provider=self.provider_name) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(f"http error: {exc}", provider=self.provider_name) from exc
            if not resp.is_redirect or not self.follow_redirects:
                return resp
            location = resp.headers.get("location")
            if not location:
                return resp
            current = str(httpx.URL(resp.url).join(location))
            self._check(current)
        raise ProviderError(
            f"too many redirects (> {self.max_redirects})", provider=self.provider_name
        )

    async def _resolve_async(
        self, client: httpx.AsyncClient, url: str
    ) -> httpx.Response:
        self._check(url)
        current = url
        for _ in range(self.max_redirects + 1):
            try:
                resp = await client.get(current)
            except httpx.ConnectError as exc:
                raise ProviderConnectionError(f"connect failed: {exc}", provider=self.provider_name) from exc
            except httpx.HTTPError as exc:
                raise ProviderError(f"http error: {exc}", provider=self.provider_name) from exc
            if not resp.is_redirect or not self.follow_redirects:
                return resp
            location = resp.headers.get("location")
            if not location:
                return resp
            current = str(httpx.URL(resp.url).join(location))
            self._check(current)
        raise ProviderError(
            f"too many redirects (> {self.max_redirects})", provider=self.provider_name
        )

    # ---------- 本文処理（抽出→切り詰め→画像） ----------

    def _build_page(
        self,
        resp: httpx.Response,
        *,
        extractor: Literal["readability", "trafilatura"],
        max_length: int,
        offset: int,
        max_images: int,
    ) -> FetchedPage:
        final_url = str(resp.url)
        mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        body = resp.content[: self.max_bytes]
        meta = ResponseMeta(provider=self.provider_name, model="-")

        if mime in _DOCUMENT_MIMES or (
            mime.startswith("application/") and not mime.endswith(("json", "xml", "xhtml+xml"))
        ):
            # 文書系・バイナリ系: 本文抽出せず、呼び出し側が DocumentParser に回す想定
            return FetchedPage(
                markdown="",
                title="",
                final_url=final_url,
                mime_type=mime,
                images=[],
                images_total=0,
                links=[],
                truncated=False,
                raw_bytes_size=len(body),
                raw_bytes=body,
                meta=meta,
            )

        try:
            html = body.decode(resp.encoding or "utf-8", errors="replace")
        except (LookupError, TypeError):
            html = body.decode("utf-8", errors="replace")

        if extractor == "trafilatura":
            title, markdown = _html_to_markdown_trafilatura(html, final_url)
        else:
            title, markdown = _html_to_markdown_readability(html, final_url)

        markdown, truncated = _slice(markdown, offset, max_length)
        images, images_total = _extract_images(html, final_url, max_images)
        links = _extract_links(html, final_url)

        return FetchedPage(
            markdown=markdown,
            title=title,
            final_url=final_url,
            mime_type=mime,
            images=images,
            images_total=images_total,
            links=links,
            truncated=truncated,
            raw_bytes_size=len(body),
            meta=meta,
        )

    # ---------- 画像のダウンロード（任意） ----------

    def _download_images_sync(
        self, client: httpx.Client, images: list[FetchedImage]
    ) -> None:
        total_budget = self.image_total_bytes
        for im in images:
            if total_budget <= 0:
                break
            try:
                if self.url_filter is not None and (reason := self.url_filter(im.url)) is not None:
                    logger.info("web_fetch: image skipped by url_filter (%s): %s", reason, im.url)
                    continue
                resp = client.get(im.url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.info("web_fetch: image fetch failed: %s (%s)", im.url, exc)
                continue
            size = min(len(resp.content), self.image_per_bytes)
            if size == 0:
                continue
            data = resp.content[:size]
            total_budget -= size
            im.mime_type = (resp.headers.get("content-type") or "").split(";")[0].strip() or None
            im.data_base64 = base64.b64encode(data).decode("ascii")

    async def _download_images_async(
        self, client: httpx.AsyncClient, images: list[FetchedImage]
    ) -> None:
        sem = asyncio.Semaphore(self.image_download_concurrency)
        total_budget = [self.image_total_bytes]

        async def one(im: FetchedImage) -> None:
            if total_budget[0] <= 0:
                return
            if self.url_filter is not None:
                reason = self.url_filter(im.url)
                if reason is not None:
                    logger.info("web_fetch: image skipped by url_filter (%s): %s", reason, im.url)
                    return
            async with sem:
                try:
                    resp = await client.get(im.url)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.info("web_fetch: image fetch failed: %s (%s)", im.url, exc)
                    return
            size = min(len(resp.content), self.image_per_bytes)
            if size == 0:
                return
            data = resp.content[:size]
            total_budget[0] -= size
            im.mime_type = (resp.headers.get("content-type") or "").split(";")[0].strip() or None
            im.data_base64 = base64.b64encode(data).decode("ascii")

        await asyncio.gather(*(one(im) for im in images))

    # ---------- 公開 API ----------

    def fetch(
        self,
        url: str,
        *,
        max_length: int = 50_000,
        offset: int = 0,
        fetch_images: bool = False,
        max_images: int = 10,
        extractor: Literal["readability", "trafilatura"] = "readability",
        timeout_seconds: float = 30.0,
        **provider_options: Any,
    ) -> FetchedPage:
        client = self._sync_client(timeout_seconds)
        resp = self._resolve_sync(client, url)
        page = self._build_page(
            resp,
            extractor=extractor,
            max_length=max_length,
            offset=offset,
            max_images=max_images,
        )
        if fetch_images and page.images:
            self._download_images_sync(client, page.images)
        return page

    async def afetch(
        self,
        url: str,
        *,
        max_length: int = 50_000,
        offset: int = 0,
        fetch_images: bool = False,
        max_images: int = 10,
        extractor: Literal["readability", "trafilatura"] = "readability",
        timeout_seconds: float = 30.0,
        **provider_options: Any,
    ) -> FetchedPage:
        client = self._async_client(timeout_seconds)
        resp = await self._resolve_async(client, url)
        page = self._build_page(
            resp,
            extractor=extractor,
            max_length=max_length,
            offset=offset,
            max_images=max_images,
        )
        if fetch_images and page.images:
            await self._download_images_async(client, page.images)
        return page
