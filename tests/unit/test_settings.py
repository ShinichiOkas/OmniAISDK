import stat
import sys
import tomllib
from pathlib import Path

import pytest

from omni_ai import ConfigurationError, OmniClient
from omni_ai.config import resolve_setting
from omni_ai.router import FallbackChatAdapter
from omni_ai.settings import (
    config_path,
    dump_toml,
    save_config,
    save_secrets,
    secrets_path,
    validate_files,
)


@pytest.fixture
def cfgdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    for var in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OMNI_GEMINI_API_KEY",
        "OLLAMA_HOST",
        "OMNI_OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


FULL = {
    "ollama": {"base_url": "http://ollama.test:11434"},
    "client": {
        "chat": {
            "providers": ["gemini", "ollama"],
            "models": {"gemini": "gemini-3.1-flash-lite", "ollama": "gemma4:31b-cloud"},
        },
        "embedding": {"provider": "fastembed"},
        "rerank": {"provider": "fastembed"},
        "search": {"provider": "ddgs", "region": "us-en"},
        "doc_parser": {"provider": "default"},
        "judge": {"provider": "ollama", "model": "tev1:0.8b"},
        "audio": {"voice": "ja-JP-KeitaNeural", "whisper_model": "small"},
        "sandbox": {"provider": "docker", "network": False},
    },
}


def test_dump_toml_roundtrip() -> None:
    text = dump_toml(FULL)
    assert tomllib.loads(text) == FULL
    assert 'models = { gemini = "gemini-3.1-flash-lite", ollama = "gemma4:31b-cloud" }' in text
    assert tomllib.loads(dump_toml({"a": {"s": 'q"\\\n'}})) == {"a": {"s": 'q"\\\n'}}
    assert tomllib.loads(dump_toml({"c": {"chat": {"x": {}}}})) == {"c": {"chat": {"x": {}}}}  # 空のテーブル


def test_paths_follow_env_and_secrets_are_private(cfgdir: Path) -> None:
    assert config_path() == cfgdir / "config.toml" and secrets_path() == cfgdir / "secrets.toml"
    p = save_secrets({"gemini": {"api_key": "AIza-test"}})
    if sys.platform != "win32":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert resolve_setting("gemini", "api_key").source == "secrets_file"


def test_resolution_order_env_beats_secrets(cfgdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_secrets({"gemini": {"api_key": "from-secrets"}})
    save_config({"gemini": {"api_key": "from-config"}})
    assert resolve_setting("gemini", "api_key").value == "from-secrets"
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert resolve_setting("gemini", "api_key").value == "from-env"


def test_from_config_builds_every_capability(cfgdir: Path) -> None:
    save_config(FULL)
    save_secrets({"gemini": {"api_key": "AIza-test"}})
    cfg, issues = validate_files()
    assert cfg is not None and [i for i in issues if i.level == "error"] == []
    ai = OmniClient.from_config()
    assert isinstance(ai.chat.inner, FallbackChatAdapter)  # type: ignore[attr-defined]
    assert [p.provider_name for p in ai.chat.inner.providers] == ["gemini", "ollama"]  # type: ignore[attr-defined]
    assert ai.chat.inner.providers[1].default_model == "gemma4:31b-cloud"  # type: ignore[attr-defined]
    assert ai.embedding.inner.provider_name == "fastembed"  # type: ignore[attr-defined]
    assert ai.search.inner.region == "us-en"  # type: ignore[attr-defined]
    assert ai.judge.inner.default_model == "tev1:0.8b"  # type: ignore[attr-defined]
    assert ai.audio.inner.speech.voice == "ja-JP-KeitaNeural"  # type: ignore[attr-defined]
    assert ai.audio.inner.transcription.model_name == "small"  # type: ignore[attr-defined]
    assert ai.sandbox.inner.provider_name == "docker"  # type: ignore[attr-defined]
    assert ai.rerank and ai.doc_parser


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"client": {"chat": {"providers": ["gemini"], "modles": {}}}}, "unknown key"),  # 誤字
        ({"client": {"chat": {"providers": ["openai"]}}}, "client.chat.providers"),  # 未対応のプロバイダー
        ({"client": {"chat": {"providers": ["ollama"]}}}, "ollama has no default model"),
        ({"client": {"embedding": {"provider": "ollama"}}}, "needs a model"),
        ({"client": {"chat": {"providers": ["gemini"]}}}, "no API key"),
    ],
)
def test_validation_reports_errors_with_location(cfgdir: Path, raw: dict, fragment: str) -> None:
    save_config(raw)
    _, issues = validate_files()
    errors = [str(i) for i in issues if i.level == "error"]
    assert any(fragment in e for e in errors), errors
    with pytest.raises(ConfigurationError):
        OmniClient.from_config()


def test_api_key_in_config_is_a_warning(cfgdir: Path) -> None:
    save_config({"gemini": {"api_key": "AIza-x"}})
    _, issues = validate_files()
    assert [i.level for i in issues] == ["warning"] and "secrets.toml" in issues[0].message


def test_invalid_toml(cfgdir: Path) -> None:
    (cfgdir / "config.toml").write_text("[client\n", encoding="utf-8")
    cfg, issues = validate_files()
    assert cfg is None and "invalid TOML" in issues[0].message


def test_empty_config_builds_empty_client(cfgdir: Path) -> None:
    ai = OmniClient.from_config()
    with pytest.raises(Exception, match="not configured"):
        _ = ai.chat
