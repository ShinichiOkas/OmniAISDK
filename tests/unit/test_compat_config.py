import pytest

from omni_ai.compat import PlatformCompat
from omni_ai.config import resolve_setting


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1", "http://127.0.0.1:11434"),
        ("0.0.0.0", "http://127.0.0.1:11434"),
        ("0.0.0.0:8080", "http://127.0.0.1:8080"),
        ("my-server:11434", "http://my-server:11434"),
        ("http://my-server", "http://my-server:80"),
        ("https://my-server", "https://my-server:443"),
        ("https://my-server:8443/ollama/", "https://my-server:8443/ollama"),
        ("[::1]:11434", "http://[::1]:11434"),
        (":11434", "http://127.0.0.1:11434"),
    ],
)
def test_normalize_ollama_host(raw: str, expected: str) -> None:
    assert PlatformCompat.normalize_ollama_host(raw) == expected


def test_resolve_ollama_base_url_prefers_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:9999")
    assert PlatformCompat.resolve_ollama_base_url("http://x:1/") == "http://x:1"
    assert PlatformCompat.resolve_ollama_base_url() == "http://127.0.0.1:9999"


def test_safe_temp_file_roundtrip_and_cleanup() -> None:
    with PlatformCompat.safe_temp_file(suffix=".bin", data=b"abc") as p:
        assert p.read_bytes() == b"abc"
        kept = p
    assert not kept.exists()


def test_resolve_setting_order() -> None:
    cfg = {"gemini": {"api_key": "from-file"}}
    env = {"GOOGLE_API_KEY": "g", "GEMINI_API_KEY": "gem", "OMNI_GEMINI_API_KEY": "omni"}
    assert resolve_setting("gemini", "api_key", "arg", environ=env, config=cfg).source == "argument"
    r = resolve_setting("gemini", "api_key", environ=env, config=cfg)
    assert (r.value, r.source) == ("omni", "omni_env")
    env.pop("OMNI_GEMINI_API_KEY")
    assert resolve_setting("gemini", "api_key", environ=env, config=cfg).value == "gem"
    assert resolve_setting("gemini", "api_key", environ={"GOOGLE_API_KEY": "g"}, config=cfg).value == "g"
    r = resolve_setting("gemini", "api_key", environ={}, config=cfg)
    assert (r.value, r.source) == ("from-file", "config_file")
    assert resolve_setting("gemini", "api_key", environ={}, config={}).source == "unset"


def test_describe_masks_secret() -> None:
    r = resolve_setting("gemini", "api_key", "AIzaSECRETSECRET", environ={}, config={})
    assert "SECRET" not in r.describe()
