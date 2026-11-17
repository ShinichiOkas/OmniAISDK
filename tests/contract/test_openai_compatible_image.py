"""OpenAI 互換の画像生成（``/v1/images/generations``）アダプターの契約テスト。

応答は OpenAI の公開仕様に沿って手で作った**合成データ**。本物の上流には一切送らない（respx）。
"""

import base64
import json
from pathlib import Path

import httpx
import pytest
import respx

from omni_ai import (
    AuthenticationError,
    InvalidRequestError,
    OmniError,
    ProviderConnectionError,
    ProviderOverloadedError,
)
from omni_ai._http import RetryPolicy
from omni_ai.providers.openai_compatible.image import OpenAICompatibleImageAdapter

BASE = "http://127.0.0.1:9100/v1"
URL = f"{BASE}/images/generations"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 12
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("OMNI_OPENAI_COMPATIBLE_API_KEY", raising=False)


def make(**kw) -> OpenAICompatibleImageAdapter:
    params = {"base_url": BASE, "retry": RetryPolicy(max_attempts=2, base_delay=0.0, jitter=0.0)}
    params.update(kw)
    return OpenAICompatibleImageAdapter(**params)


def ok(*datas: bytes, extra: dict | None = None) -> httpx.Response:
    items = [{"b64_json": base64.b64encode(d).decode(), **(extra or {})} for d in datas]
    return httpx.Response(200, json={"created": 1760000000, "data": items}, headers={"x-request-id": "req-1"})


@respx.mock
def test_request_is_openai_standard_shape_and_response_decoded() -> None:
    route = respx.post(URL).mock(return_value=ok(PNG))
    res = make().generate_image("a red fox", size="1536x1024", n=1, model="img-1")
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "prompt": "a red fox",
        "n": 1,
        "size": "1536x1024",
        "model": "img-1",
        "response_format": "b64_json",
    }
    assert [i.data for i in res.images] == [PNG]
    assert res.images[0].mime_type == "image/png"
    assert res.meta.provider == "openai-compatible"
    assert res.meta.model == "img-1"
    assert res.meta.request_id == "req-1"
    assert res.meta.attempts == 1


@respx.mock
def test_model_omitted_from_body_when_not_given() -> None:
    route = respx.post(URL).mock(return_value=ok(PNG))
    make().generate_image("x")
    sent = json.loads(route.calls.last.request.content)
    assert "model" not in sent
    assert sent["size"] == "1024x1024"  # 寸法の表は持たない。呼び出し側の既定がそのまま出る


@respx.mock
def test_default_model_and_multiple_images_and_mime_sniff_and_revised_prompt() -> None:
    route = respx.post(URL).mock(return_value=ok(PNG, JPEG, extra={"revised_prompt": "rp"}))
    res = make(default_model="dm").generate_image("x", n=2)
    assert json.loads(route.calls.last.request.content)["model"] == "dm"
    assert [i.mime_type for i in res.images] == ["image/png", "image/jpeg"]
    assert all(i.revised_prompt == "rp" for i in res.images)


@respx.mock
def test_size_is_passed_through_not_validated() -> None:
    route = respx.post(URL).mock(return_value=ok(PNG))
    make().generate_image("x", size="123x45")
    assert json.loads(route.calls.last.request.content)["size"] == "123x45"


@respx.mock
def test_url_response_is_fetched_without_authorization() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"created": 1, "data": [{"url": "http://cdn.example/a.jpg"}]})
    )
    fetch = respx.get("http://cdn.example/a.jpg").mock(
        return_value=httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
    )
    res = make(api_key="SECRET").generate_image("x")
    assert res.images[0].data == JPEG
    assert res.images[0].mime_type == "image/jpeg"
    assert "authorization" not in fetch.calls.last.request.headers


@respx.mock
def test_mixed_b64_and_url_keep_order() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"url": "http://cdn.example/1"}, {"b64_json": base64.b64encode(PNG).decode()}],
            },
        )
    )
    respx.get("http://cdn.example/1").mock(return_value=httpx.Response(200, content=JPEG))
    res = make().generate_image("x", n=2)
    assert [i.data for i in res.images] == [JPEG, PNG]
    # content-type が無い URL 取得は先頭バイトで判定する
    assert [i.mime_type for i in res.images] == ["image/jpeg", "image/png"]


@respx.mock
def test_url_fetch_failure_is_an_error_not_empty_image() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"data": [{"url": "http://cdn.example/x"}]}))
    respx.get("http://cdn.example/x").mock(
        return_value=httpx.Response(404, json={"error": {"message": "gone"}})
    )
    with pytest.raises(OmniError):
        make().generate_image("x")


@respx.mock
def test_api_key_comes_from_the_shared_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_OPENAI_COMPATIBLE_API_KEY", "sk-shared")
    route = respx.post(URL).mock(return_value=ok(PNG))
    make().generate_image("x")
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-shared"


@respx.mock
def test_no_key_means_no_authorization_header() -> None:
    route = respx.post(URL).mock(return_value=ok(PNG))
    make().generate_image("x")
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_provider_options_namespace_is_merged_and_others_dropped() -> None:
    route = respx.post(URL).mock(return_value=ok(PNG))
    make().generate_image("x", **{"openai-compatible": {"quality": "hd"}, "gemini": {"zzz": 1}})
    sent = json.loads(route.calls.last.request.content)
    assert sent["quality"] == "hd"
    assert "zzz" not in sent


@respx.mock
@pytest.mark.parametrize(
    ("status", "exc"),
    [(401, AuthenticationError), (400, InvalidRequestError), (502, ProviderOverloadedError)],
)
def test_http_errors_map_to_omni_errors(status: int, exc: type[Exception]) -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(status, json={"error": {"message": "nope", "type": "api_error"}})
    )
    with pytest.raises(exc) as ei:
        make().generate_image("x")
    assert "nope" in str(ei.value)


@respx.mock
def test_retries_on_502_then_succeeds() -> None:
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(502, json={"error": {"message": "bad gw"}}), ok(PNG)]
    )
    res = make().generate_image("x")
    assert route.call_count == 2
    assert res.meta.attempts == 2


@respx.mock
def test_connection_error_is_provider_connection_error() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderConnectionError):
        make(retry=RetryPolicy(max_attempts=1)).generate_image("x")


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [{"created": 1, "data": []}, {"created": 1}, {"data": [{"foo": "bar"}]}, []],
)
def test_empty_or_unusable_response_is_an_error(payload) -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload))
    with pytest.raises(OmniError):
        make().generate_image("x")


@respx.mock
def test_invalid_base64_is_an_error() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"data": [{"b64_json": "!!!notb64"}]}))
    with pytest.raises(OmniError):
        make().generate_image("x")


@pytest.mark.parametrize(("prompt", "n"), [("", 1), ("   ", 1), ("x", 0)])
def test_bad_arguments_do_not_reach_the_network(prompt: str, n: int) -> None:
    with respx.mock(assert_all_called=False) as m:
        route = m.post(URL).mock(return_value=ok(PNG))
        with pytest.raises(InvalidRequestError):
            make().generate_image(prompt, n=n)
        assert route.call_count == 0


def test_base_url_is_required_and_normalized() -> None:
    with pytest.raises(InvalidRequestError):
        OpenAICompatibleImageAdapter()
    assert OpenAICompatibleImageAdapter(base_url="http://h:1/").base_url == "http://h:1/v1"
    assert OpenAICompatibleImageAdapter(base_url="http://h:1/v1/").base_url == "http://h:1/v1"


@respx.mock
async def test_async_generate_image() -> None:
    route = respx.post(URL).mock(return_value=ok(PNG))
    res = await make().agenerate_image("a fox", size="512x512", n=1)
    assert json.loads(route.calls.last.request.content)["size"] == "512x512"
    assert res.images[0].data == PNG


@respx.mock
async def test_async_url_response_fetched() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"data": [{"url": "http://cdn.example/a"}]}))
    respx.get("http://cdn.example/a").mock(return_value=httpx.Response(200, content=PNG))
    res = await make().agenerate_image("x")
    assert res.images[0].data == PNG


@respx.mock
async def test_async_error_mapping() -> None:
    respx.post(URL).mock(return_value=httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(AuthenticationError):
        await make().agenerate_image("x")


@respx.mock
def test_b64_that_decodes_to_nothing_is_an_error() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"data": [{"b64_json": "===="}]}))
    with pytest.raises(OmniError):
        make().generate_image("x")


@respx.mock
def test_url_with_empty_body_is_an_error() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"data": [{"url": "http://cdn.example/e"}]}))
    respx.get("http://cdn.example/e").mock(return_value=httpx.Response(200, content=b""))
    with pytest.raises(OmniError):
        make().generate_image("x")


@respx.mock
def test_invalid_b64_before_a_url_entry_is_an_error_not_a_misplaced_image() -> None:
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": "!!!"}, {"url": "http://cdn.example/u"}]})
    )
    respx.get("http://cdn.example/u").mock(return_value=httpx.Response(200, content=JPEG))
    with pytest.raises(OmniError):
        make().generate_image("x", n=2)


@respx.mock
@pytest.mark.parametrize("kind", ["image", "search"])
def test_200_with_non_json_body_is_an_omni_error(kind: str) -> None:
    from omni_ai.providers.openai_compatible.search import OpenAICompatibleSearchAdapter

    respx.post(URL).mock(return_value=httpx.Response(200, content=b"<html>wrong base url</html>"))
    respx.post(f"{BASE}/search").mock(
        return_value=httpx.Response(200, content=b"<html>wrong base url</html>")
    )
    with pytest.raises(OmniError):
        if kind == "image":
            make().generate_image("x")
        else:
            OpenAICompatibleSearchAdapter(base_url=BASE).search("x")


@respx.mock
async def test_async_url_fetch_sends_no_authorization() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"data": [{"url": "http://cdn.example/k"}]}))
    fetch = respx.get("http://cdn.example/k").mock(return_value=httpx.Response(200, content=PNG))
    await make(api_key="SECRET").agenerate_image("x")
    assert "authorization" not in fetch.calls.last.request.headers


def test_adapters_are_reachable_from_the_providers_package() -> None:
    from omni_ai.providers import OpenAICompatibleImageAdapter as I
    from omni_ai.providers import OpenAICompatibleSearchAdapter as S

    assert I is OpenAICompatibleImageAdapter
    assert S.__name__ == "OpenAICompatibleSearchAdapter"
