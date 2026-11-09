"""Web 取得で使う型。

- :class:`FetchedImage`: ページから抽出した画像 1 枚
- :class:`FetchedPage`: 1 回の取得で返る結果
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import ResponseMeta

__all__ = ["FetchedImage", "FetchedPage"]


class FetchedImage(BaseModel):
    """ページから抽出した画像 1 枚。``data`` は ``fetch_images=True`` のときだけ入る。"""

    url: str
    alt: str | None = None
    mime_type: str | None = None
    score: float | None = None
    data_base64: str | None = None


class FetchedPage(BaseModel):
    """Web 取得の結果（1 URL あたり 1 本）。

    - ``markdown``: 本文（抽出・整形後）。切り詰めた場合も ``truncated=True`` にして**途中まで**を入れる
    - ``title``: ページのタイトル（抽出できないときは空）
    - ``final_url``: リダイレクトの最後の URL
    - ``mime_type``: 応答の ``Content-Type``
    - ``images``: 抽出した画像の一覧（``fetch_images=True`` のときは ``data_base64`` も埋まる）
    - ``images_total``: 絞り込む前の件数（LLM が「ほかにもあります」と言えるように）
    - ``links``: 外向きリンクの一覧（本文抽出で拾えた分）
    - ``truncated``: ``max_length`` で切ったか
    - ``raw_bytes_size``: 取ってきたバイト列の大きさ（本体サイズ）
    """

    markdown: str = ""
    title: str = ""
    final_url: str = ""
    mime_type: str = ""
    images: list[FetchedImage] = Field(default_factory=list)
    images_total: int = 0
    links: list[str] = Field(default_factory=list)
    truncated: bool = False
    raw_bytes_size: int = 0
    # 文書系 MIME（PDF・docx 等）のとき、呼び出し側が DocumentParser に渡すためのバイト列
    raw_bytes: bytes | None = None
    meta: ResponseMeta
