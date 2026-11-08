import io
import stat
import sys
from pathlib import Path

import pytest

from omni_ai import OmniClient
from omni_ai.cli import IO
from omni_ai.cli import setup as menu
from omni_ai.settings import load_config, load_secrets

# 用途（capability）ごとの Ollama のモデル（実測の形）
BY_CAPABILITY = {
    "completion": ["gemma4:31b-cloud"],
    "decision": ["tev1:0.8b", "kev:0.8b-q8_0"],
    "embedding": ["nomic-embed-text:latest"],
}


@pytest.fixture
def cfgdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    for var in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OMNI_GEMINI_API_KEY",
        "OLLAMA_HOST",
        "OLLAMA_API_KEY",
        "XAI_API_KEY",
        "AGNES_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        menu, "_ollama_model_names", lambda capability=None: BY_CAPABILITY.get(capability or "", [])
    )
    monkeypatch.setattr(menu, "_remote_chat_models", lambda name: [])
    return tmp_path


def scripted(answers: list[str]) -> tuple[IO, io.StringIO]:
    out = io.StringIO()
    it = iter(answers)

    def ask(prompt: str) -> str:
        out.write(prompt)
        return next(it)

    return IO(out=out, ask=ask), out


def test_gemini_key_goes_to_private_secrets_file(cfgdir: Path) -> None:
    io_, _ = scripted(["1", "AIza-secret", "0"])  # プロバイダー → Gemini のキー → 戻る
    menu.menu_providers(io_)
    assert load_secrets()["gemini"]["api_key"] == "AIza-secret"
    assert "api_key" not in str(load_config())
    if sys.platform != "win32":
        assert stat.S_IMODE((cfgdir / "secrets.toml").stat().st_mode) == 0o600


def test_chat_order_and_models_then_from_config(cfgdir: Path) -> None:
    # ollama を 1 番目（モデルは一覧の 1 番）、gemini を 2 番目（既定のモデル）、0 で決定
    io_, out = scripted(["2", "1", "1", "", "0"])
    menu.menu_chat(io_)
    chat = load_config()["client"]["chat"]
    assert chat == {"providers": ["ollama", "gemini"], "models": {"ollama": "gemma4:31b-cloud"}}
    assert "no API key" in out.getvalue()  # gemini を選んだがキーが無い → 検証で知らせる
    menu.menu_providers(scripted(["1", "AIza-x"])[0])
    ai = OmniClient.from_config()
    assert [p.provider_name for p in ai.chat.inner.providers] == ["ollama", "gemini"]  # type: ignore[attr-defined]


def test_capabilities_judge_audio_and_removal(cfgdir: Path) -> None:
    menu.menu_capability(scripted(["6", "1", "1"])[0])  # judge → ollama → tev1:0.8b
    assert load_config()["client"]["judge"] == {"provider": "ollama", "model": "tev1:0.8b"}
    menu.menu_capability(scripted(["7", "1", "", "2"])[0])  # audio → default → 声は既定 → small
    assert load_config()["client"]["audio"] == {"whisper_model": "small"}
    io_, out = scripted(["2", "1"])  # embedding → fastembed
    menu.menu_capability(io_)
    assert "[local]" in out.getvalue()
    menu.menu_capability(scripted(["6", "0"])[0])  # judge を「使わない」→ 消える
    assert "judge" not in load_config()["client"]


def test_extra_requirements_from_metadata() -> None:
    assert any(r.startswith("fastembed") for r in menu.extra_requirements("local"))
    assert any(r.startswith("faster-whisper") for r in menu.extra_requirements("audio"))
    assert menu.extra_requirements("no-such-extra") == []


def test_extras_install_can_be_cancelled(cfgdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(menu, "_pip_command", lambda: ["pip", "install"])
    monkeypatch.setattr(menu.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    io_, out = scripted(["1", "n"])
    menu.menu_extras(io_)
    assert "実行するコマンド: pip install fastembed" in out.getvalue() and ran == []


def test_main_menu_survives_errors_and_exits(cfgdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("broken")

    monkeypatch.setattr(menu, "menu_models", lambda io: boom())
    io_, out = scripted(["9", "5", "0"])
    assert menu.run_setup(io_) == 0
    text = out.getvalue()
    assert "番号で選んでください" in text and "エラー: RuntimeError: broken" in text


def test_cloud_and_local_compatible_providers(cfgdir: Path) -> None:
    # プロバイダー → 3) Ollama Cloud のキー / 4) xAI のキー
    menu.menu_providers(scripted(["3", "oc-key"])[0])
    menu.menu_providers(scripted(["4", "xai-key"])[0])
    # OpenAI 互換サーバーの接続先（最後の項目）
    options = 2 + len(menu._KEY_PROVIDERS) - 1 + 1
    menu.menu_providers(scripted([str(options), "http://127.0.0.1:8080"])[0])
    assert load_secrets()["ollama-cloud"]["api_key"] == "oc-key"
    assert load_config()["openai-compatible"]["base_url"] == "http://127.0.0.1:8080"
    # chat: xai（4 番目）→ モデル手入力、openai-compatible（残りの末尾）→ 手入力、ollama-cloud
    names = list(menu.CHAT_PROVIDERS)
    first = names.index("xai") + 1
    rest = [n for n in names if n != "xai"]
    second = rest.index("openai-compatible") + 1
    rest.remove("openai-compatible")
    third = rest.index("ollama-cloud") + 1
    io_, out = scripted([str(first), "grok-4", str(second), "local", str(third), "gpt-oss:120b", "0"])
    menu.menu_chat(io_)
    chat = load_config()["client"]["chat"]
    assert chat["providers"] == ["xai", "openai-compatible", "ollama-cloud"]
    assert "設定に問題はありません" in out.getvalue()
    ai = OmniClient.from_config()
    providers = [p.provider_name for p in ai.chat.inner.providers]  # type: ignore[attr-defined]
    assert providers == ["xai", "openai-compatible", "ollama-cloud"]
    text = (cfgdir / "config.toml").read_text()
    assert "[openai-compatible]" in text and '"openai-compatible"' in text
