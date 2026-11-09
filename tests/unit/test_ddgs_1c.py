"""1.C: min_interval_seconds・cancel_check・search_images の単体試験。

`FakeDDGS` は tests/contract/test_ddgs.py と同じ形の偽物。ここでは `images()` も持つ版を自前で作る。
"""

from __future__ import annotations

import pytest

from omni_ai import InvalidRequestError, OmniError
from omni_ai._http import RetryPolicy
from omni_ai.providers.ddgs import DDGSSearchAdapter
from omni_ai.types import ImageSearchResponse


class FakeDDGS:
    """text / images の両方を返す偽物。script は『呼び出しごとの戻り値』を順に積む。"""

    def __init__(self, text_script=None, images_script=None) -> None:
        self.text_script = list(text_script or [])
        self.images_script = list(images_script or [])
        self.text_calls: list[dict] = []
        self.images_calls: list[dict] = []

    def _factory(self):
        parent = self

        class _Client:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return None

            def text(self_inner, query, **kwargs):
                parent.text_calls.append({"query": query, **kwargs})
                if not parent.text_script:
                    return []
                item = parent.text_script.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

            def images(self_inner, query, **kwargs):
                parent.images_calls.append({"query": query, **kwargs})
                if not parent.images_script:
                    return []
                item = parent.images_script.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        def make(**_kwargs):
            return _Client()

        return make


def _adapter(fake: FakeDDGS, **kw) -> DDGSSearchAdapter:
    return DDGSSearchAdapter(
        ddgs_factory=fake._factory(),
        sleep=lambda _s: None,
        retry=RetryPolicy(max_attempts=2, base_delay=0.0),
        **kw,
    )


# ---------- S5: min_interval_seconds ----------


def test_min_interval_waits_between_calls() -> None:
    fake = FakeDDGS(text_script=[[{"title": "a", "href": "https://a", "body": ""}], [{"title": "b", "href": "https://b", "body": ""}]])
    clock = [100.0]
    sleeps: list[float] = []

    def fake_sleep(d: float) -> None:
        sleeps.append(d)
        clock[0] += d

    def mono() -> float:
        return clock[0]

    ad = DDGSSearchAdapter(
        ddgs_factory=fake._factory(),
        sleep=fake_sleep,
        monotonic=mono,
        retry=RetryPolicy(max_attempts=1, base_delay=0.0),
        min_interval_seconds=1.0,
    )
    ad.search("first")
    # 2 回目は 1.0 秒間隔が要るので、sleep が呼ばれるはず
    ad.search("second")
    assert sum(sleeps) == pytest.approx(1.0, abs=0.2)
    assert len(fake.text_calls) == 2


def test_min_interval_zero_does_not_sleep() -> None:
    fake = FakeDDGS(text_script=[[], []])
    sleeps: list[float] = []
    ad = DDGSSearchAdapter(
        ddgs_factory=fake._factory(),
        sleep=lambda d: sleeps.append(d),
        retry=RetryPolicy(max_attempts=1, base_delay=0.0),
        min_interval_seconds=0.0,
    )
    ad.search("a")
    ad.search("b")
    assert sleeps == []


# ---------- S5: cancel_check ----------


def test_cancel_check_before_call_raises() -> None:
    fake = FakeDDGS(text_script=[[{"title": "x", "href": "https://x", "body": ""}]])
    cancelled = {"on": True}

    ad = DDGSSearchAdapter(
        ddgs_factory=fake._factory(),
        sleep=lambda _s: None,
        retry=RetryPolicy(max_attempts=1, base_delay=0.0),
        cancel_check=lambda: cancelled["on"],
    )
    with pytest.raises(OmniError, match="cancelled"):
        ad.search("q")
    assert fake.text_calls == []  # ddgs を呼んでいない


def test_cancel_check_during_throttle_wait_raises() -> None:
    fake = FakeDDGS(text_script=[[], []])
    clock = [0.0]
    cancelled = {"on": False}

    def fake_sleep(d: float) -> None:
        clock[0] += d
        # 待っている途中で中断が立つ
        cancelled["on"] = True

    ad = DDGSSearchAdapter(
        ddgs_factory=fake._factory(),
        sleep=fake_sleep,
        monotonic=lambda: clock[0],
        retry=RetryPolicy(max_attempts=1, base_delay=0.0),
        min_interval_seconds=10.0,
        cancel_check=lambda: cancelled["on"],
    )
    ad.search("first")  # これで last_call_at が立つ
    with pytest.raises(OmniError, match="cancelled"):
        ad.search("second")


# ---------- S2: search_images ----------


def test_search_images_maps_fields() -> None:
    raw = [
        {
            "title": "a photo",
            "image": "https://img.example/a.jpg",
            "thumbnail": "https://img.example/a-thumb.jpg",
            "width": 1024,
            "height": 768,
            "source": "example.com",
        },
        {"title": "no url entry", "image": ""},  # 落ちる
        {"title": "dup", "image": "https://img.example/a.jpg"},  # 重複で落ちる
        {"title": "b", "image": "https://img.example/b.png", "width": None, "height": None},
    ]
    fake = FakeDDGS(images_script=[raw])
    ad = _adapter(fake)
    res = ad.search_images("cats", max_results=10)
    assert isinstance(res, ImageSearchResponse)
    assert [i.image_url for i in res.results] == [
        "https://img.example/a.jpg",
        "https://img.example/b.png",
    ]
    first = res.results[0]
    assert first.width == 1024 and first.height == 768
    assert first.thumbnail == "https://img.example/a-thumb.jpg"
    assert first.source == "example.com"
    assert res.meta.provider == "ddgs"
    # 呼び出しに渡した引数
    call = fake.images_calls[0]
    assert call["query"] == "cats" and call["max_results"] == 10
    assert call["region"] == "ja-jp" and call["safesearch"] == "on"


def test_search_images_empty_query_raises() -> None:
    fake = FakeDDGS()
    ad = _adapter(fake)
    with pytest.raises(InvalidRequestError, match="empty"):
        ad.search_images("")


def test_search_images_passes_ddgs_specific_options() -> None:
    fake = FakeDDGS(images_script=[[]])
    ad = _adapter(fake)
    ad.search_images(
        "cats",
        max_results=3,
        ddgs={
            "size": "Large",
            "color": "Monochrome",
            "type_image": "photo",
            "layout": "Wide",
            "license_image": "Public",
            "timelimit": "w",
        },
    )
    call = fake.images_calls[0]
    assert call["size"] == "Large"
    assert call["color"] == "Monochrome"
    assert call["type_image"] == "photo"
    assert call["layout"] == "Wide"
    assert call["license_image"] == "Public"
    assert call["timelimit"] == "w"
