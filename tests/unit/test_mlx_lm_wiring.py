"""mlx-lm が設定・環境変数・builder・CLI のメニューまで配線されていることの試験。

mlx-lm 本体は使わない（どの OS でも走る）。機械の判定は monkeypatch で切り替える。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from omni_ai import UnsupportedFeatureError
from omni_ai.builder import build_client
from omni_ai.cli import IO
from omni_ai.cli import setup as menu
from omni_ai.env_overlay import apply_env_overlay
from omni_ai.providers import mlx_lm as mod
from omni_ai.settings import CHAT_PROVIDERS, load_config, parse_config


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))


def test_settings_accept_mlx_lm_and_require_a_model() -> None:
    info = CHAT_PROVIDERS["mlx-lm"]
    assert not info.key_required and info.needs_model and not info.needs_base_url
    cfg, issues = parse_config(
        {"client": {"chat": {"providers": ["mlx-lm"], "models": {"mlx-lm": "~/models/gemma"}}}}
    )
    assert cfg is not None and not issues
    _, issues = parse_config({"client": {"chat": {"providers": ["mlx-lm"]}}})
    assert any(i.level == "error" and "mlx-lm" in i.message for i in issues)


def test_builder_makes_mlx_adapter_on_apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "is_supported_platform", lambda: True)
    cfg, _ = apply_env_overlay(
        parse_config({})[0], environ={"OMNI_CHAT_PROVIDER": "mlx-lm", "OMNI_CHAT_MODEL": "org/model-4bit"}
    )
    ai = build_client(cfg, env_overlay=False)
    inner: Any = getattr(ai.chat, "inner", ai.chat)
    assert isinstance(inner, mod.MLXLMChatAdapter)
    assert inner.default_model == "org/model-4bit" and inner.loaded_model is None  # 読み込みは初回利用時


def test_builder_refuses_on_other_machines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "is_supported_platform", lambda: False)
    cfg, _ = parse_config({"client": {"chat": {"providers": ["mlx-lm"], "models": {"mlx-lm": "m"}}}})
    assert cfg is not None
    with pytest.raises(UnsupportedFeatureError, match="Apple Silicon"):
        build_client(cfg, env_overlay=False)


def _scripted(answers: list[str]) -> tuple[IO, io.StringIO]:
    out = io.StringIO()
    it = iter(answers)

    def ask(prompt: str) -> str:
        out.write(prompt)
        return next(it)

    return IO(out=out, ask=ask), out


def test_menu_shows_mlx_lm_only_on_apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "is_supported_platform", lambda: False)
    assert "mlx-lm" not in menu._chat_choices()
    monkeypatch.setattr(mod, "is_supported_platform", lambda: True)
    choices = menu._chat_choices()
    assert choices[-1] == "mlx-lm"  # 末尾に追加（既存の番号を動かさない）
    assert choices[:-1] == [p for p in CHAT_PROVIDERS if p != "mlx-lm"]


def test_menu_chat_picks_mlx_lm_with_a_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "is_supported_platform", lambda: True)
    monkeypatch.setattr(menu, "_remote_chat_models", lambda name: [])
    pick = str(menu._chat_choices().index("mlx-lm") + 1)
    io_, out = _scripted([pick, "~/models/gemma-4-e2b-it-4bit", "0"])
    menu.menu_chat(io_)
    chat = load_config()["client"]["chat"]
    assert chat == {"providers": ["mlx-lm"], "models": {"mlx-lm": "~/models/gemma-4-e2b-it-4bit"}}
    assert "mlx-community/gemma-4-e2b-it-4bit" in out.getvalue()  # 例を示す
