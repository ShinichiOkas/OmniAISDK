"""OMNI_SEARCH_* / OMNI_IMAGE_* の環境変数（OpenAI 互換の検索・画像の口）の配線の試験。

env_overlay → settings → builder → 実際の HTTP 呼び出しまでを、偽の上流（respx）で通す。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
import respx

from omni_ai.builder import build_client
from omni_ai.env_overlay import apply_env_overlay, summarize_env_overrides
from omni_ai.providers.ddgs import DDGSSearchAdapter
from omni_ai.providers.openai_compatible.image import OpenAICompatibleImageAdapter
from omni_ai.providers.openai_compatible.search import OpenAICompatibleSearchAdapter
from omni_ai.settings import ClientSection, ImageSection, OmniConfig, SearchSection

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 12
SEARCH_BASE = "http://127.0.0.1:9201/v1"
IMAGE_BASE = "http://127.0.0.1:9202/v1"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    for var in (
        "OMNI_SEARCH_PROVIDER",
        "OMNI_SEARCH_BASE_URL",
        "OMNI_SEARCH_REGION",
        "OMNI_SEARCH_SAFESEARCH",
        "OMNI_IMAGE_PROVIDER",
        "OMNI_IMAGE_BASE_URL",
        "OMNI_OPENAI_COMPATIBLE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def unwrap(cap):
    for _ in range(5):
        if hasattr(cap, "inner"):
            cap = cap.inner
        else:
            break
    return cap


# ----------------------------------------------------------------- overlay


def test_search_openai_compatible_overlay_sets_section_and_summary() -> None:
    cfg, summary = apply_env_overlay(
        OmniConfig(),
        environ={"OMNI_SEARCH_PROVIDER": "openai-compatible", "OMNI_SEARCH_BASE_URL": SEARCH_BASE},
    )
    s = cfg.client.search
    assert s is not None
    assert (s.provider, s.base_url) == ("openai-compatible", SEARCH_BASE)
    assert {(i[0] if isinstance(i, tuple) else i.env) for i in summary.items} >= {
        "OMNI_SEARCH_PROVIDER",
        "OMNI_SEARCH_BASE_URL",
    }
    listed = " | ".join(
        summarize_env_overrides(
            {"OMNI_SEARCH_PROVIDER": "openai-compatible", "OMNI_SEARCH_BASE_URL": SEARCH_BASE}
        )
    )
    assert "OMNI_SEARCH_BASE_URL" in listed


def test_search_openai_compatible_without_base_url_raises() -> None:
    with pytest.raises(ValueError, match="OMNI_SEARCH_BASE_URL"):
        apply_env_overlay(OmniConfig(), environ={"OMNI_SEARCH_PROVIDER": "openai-compatible"})


def test_search_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="OMNI_SEARCH_PROVIDER"):
        apply_env_overlay(OmniConfig(), environ={"OMNI_SEARCH_PROVIDER": "nope"})


def test_search_base_url_alone_is_refused_not_silently_ignored() -> None:
    with pytest.raises(ValueError, match="OMNI_SEARCH_BASE_URL"):
        apply_env_overlay(OmniConfig(), environ={"OMNI_SEARCH_BASE_URL": SEARCH_BASE})


def test_search_base_url_with_toml_openai_compatible_provider_is_accepted() -> None:
    base = OmniConfig(
        client=ClientSection(search=SearchSection(provider="openai-compatible", base_url="http://old/v1"))
    )
    cfg, _ = apply_env_overlay(base, environ={"OMNI_SEARCH_BASE_URL": SEARCH_BASE})
    assert cfg.client.search is not None
    assert cfg.client.search.base_url == SEARCH_BASE


def test_search_region_only_overlay_keeps_ddgs_behaviour() -> None:
    cfg, _ = apply_env_overlay(OmniConfig(), environ={"OMNI_SEARCH_REGION": "us-en"})
    s = cfg.client.search
    assert s is not None
    assert (s.provider, s.region, s.base_url, s.backend) == ("ddgs", "us-en", None, None)


def test_search_section_validator_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        SearchSection(provider="openai-compatible")


def test_image_overlay_sets_section() -> None:
    cfg, summary = apply_env_overlay(
        OmniConfig(),
        environ={"OMNI_IMAGE_PROVIDER": "openai-compatible", "OMNI_IMAGE_BASE_URL": IMAGE_BASE},
    )
    im = cfg.client.image
    assert im is not None
    assert (im.provider, im.base_url) == ("openai-compatible", IMAGE_BASE)
    assert len(summary.items) == 2
    listed = " | ".join(
        summarize_env_overrides(
            {"OMNI_IMAGE_PROVIDER": "openai-compatible", "OMNI_IMAGE_BASE_URL": IMAGE_BASE}
        )
    )
    assert "OMNI_IMAGE_BASE_URL" in listed


def test_image_without_base_url_raises() -> None:
    with pytest.raises(ValueError, match="OMNI_IMAGE_BASE_URL"):
        apply_env_overlay(OmniConfig(), environ={"OMNI_IMAGE_PROVIDER": "openai-compatible"})


def test_image_base_url_alone_raises() -> None:
    with pytest.raises(ValueError, match="OMNI_IMAGE_PROVIDER"):
        apply_env_overlay(OmniConfig(), environ={"OMNI_IMAGE_BASE_URL": IMAGE_BASE})


def test_image_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="OMNI_IMAGE_PROVIDER"):
        apply_env_overlay(
            OmniConfig(), environ={"OMNI_IMAGE_PROVIDER": "dall-e", "OMNI_IMAGE_BASE_URL": IMAGE_BASE}
        )


def test_image_section_validator_requires_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        ImageSection(provider="openai-compatible")


def test_no_env_leaves_search_and_image_unset() -> None:
    cfg, summary = apply_env_overlay(OmniConfig(), environ={})
    assert cfg.client.search is None
    assert cfg.client.image is None
    assert summary.is_empty()


# ----------------------------------------------------------------- builder


def test_builder_default_search_is_still_ddgs() -> None:
    cfg = OmniConfig(client=ClientSection(search=SearchSection()))
    assert isinstance(unwrap(build_client(cfg).search), DDGSSearchAdapter)


def test_builder_builds_openai_compatible_search_and_image_from_toml() -> None:
    cfg = OmniConfig(
        client=ClientSection(
            search=SearchSection(provider="openai-compatible", base_url=SEARCH_BASE, backend="ddgs"),
            image=ImageSection(provider="openai-compatible", base_url=IMAGE_BASE, model="img-1"),
        )
    )
    client = build_client(cfg)
    s = unwrap(client.search)
    im = unwrap(client.image)
    assert isinstance(s, OpenAICompatibleSearchAdapter)
    assert (s.base_url, s.backend) == (SEARCH_BASE, "ddgs")
    assert isinstance(im, OpenAICompatibleImageAdapter)
    assert (im.base_url, im.default_model) == (IMAGE_BASE, "img-1")


@respx.mock
def test_env_to_http_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_SEARCH_PROVIDER", "openai-compatible")
    monkeypatch.setenv("OMNI_SEARCH_BASE_URL", SEARCH_BASE)
    monkeypatch.setenv("OMNI_IMAGE_PROVIDER", "openai-compatible")
    monkeypatch.setenv("OMNI_IMAGE_BASE_URL", IMAGE_BASE)
    monkeypatch.setenv("OMNI_OPENAI_COMPATIBLE_API_KEY", "sk-test")
    search_route = respx.post(f"{SEARCH_BASE}/search").mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "t", "url": "https://x", "snippet": "s"}]}
        )
    )
    image_route = respx.post(f"{IMAGE_BASE}/images/generations").mock(
        return_value=httpx.Response(
            200, json={"created": 1, "data": [{"b64_json": base64.b64encode(PNG).decode()}]}
        )
    )
    client = build_client(OmniConfig())
    found = client.search.search("hello", max_results=2)
    img = client.image.generate_image("a cat", size="512x512")
    assert [r.url for r in found.results] == ["https://x"]
    assert json.loads(search_route.calls.last.request.content) == {"query": "hello", "max_results": 2}
    assert search_route.calls.last.request.headers["authorization"] == "Bearer sk-test"
    assert img.images[0].data == PNG
    assert json.loads(image_route.calls.last.request.content)["size"] == "512x512"
    assert image_route.calls.last.request.headers["authorization"] == "Bearer sk-test"
