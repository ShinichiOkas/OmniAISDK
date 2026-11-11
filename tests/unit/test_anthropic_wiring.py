"""Anthropic が設定・環境変数・builder・CLI のメニューまで配線されていることの試験。

HTTP は偽の上流（respx）。応答は手で作った合成データ（実 API の記録ではない）。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from omni_ai import ChatMessage
from omni_ai.builder import build_client
from omni_ai.cli import setup as menu
from omni_ai.config import resolve_setting
from omni_ai.env_overlay import apply_env_overlay
from omni_ai.providers.anthropic import AnthropicChatAdapter
from omni_ai.settings import CHAT_PROVIDERS, parse_config


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNI_CONFIG_DIR", str(tmp_path))
    for var in ("ANTHROPIC_API_KEY", "OMNI_ANTHROPIC_API_KEY", "OMNI_ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def test_settings_accept_anthropic_and_require_a_model() -> None:
    assert CHAT_PROVIDERS["anthropic"].key_required
    ok, issues = parse_config(
        {"client": {"chat": {"providers": ["anthropic"], "models": {"anthropic": "claude-sonnet-5-5"}}}}
    )
    assert ok is not None and not [i for i in issues if i.level == "error"]
    _, issues = parse_config({"client": {"chat": {"providers": ["anthropic"]}}})
    assert any(i.level == "error" and "models.anthropic" in i.message for i in issues)
    cfg, issues = parse_config({"anthropic": {"api_key": "sk-x"}})
    assert cfg is not None and cfg.connection("anthropic").api_key == "sk-x"
    assert any(i.level == "warning" and "secrets.toml" in i.message for i in issues)


def test_standard_env_var_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "std")
    assert resolve_setting("anthropic", "api_key").value == "std"
    monkeypatch.setenv("OMNI_ANTHROPIC_API_KEY", "omni")
    assert resolve_setting("anthropic", "api_key").value == "omni"  # OMNI_ が先


@respx.mock
def test_env_overlay_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-env")
    cfg, _ = apply_env_overlay(
        parse_config({})[0],
        environ={
            "OMNI_CHAT_PROVIDER": "anthropic",
            "OMNI_CHAT_MODEL": "claude-haiku-5-5",
            "OMNI_CHAT_BASE_URL": "https://gw.example.com",
        },
    )
    route = respx.post("https://gw.example.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    ai = build_client(cfg, env_overlay=False)
    res = ai.chat.generate([ChatMessage.user("x")])
    assert res.content == "hi" and res.meta.provider == "anthropic"
    req = route.calls[0].request
    assert req.headers["x-api-key"] == "k-env"
    assert json.loads(req.content)["model"] == "claude-haiku-5-5"


def test_builder_makes_anthropic_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg, _ = parse_config(
        {"client": {"chat": {"providers": ["anthropic"], "models": {"anthropic": "claude-opus-5-5"}}}}
    )
    ai = build_client(cfg, env_overlay=False)
    inner = ai.chat.inner  # type: ignore[attr-defined]
    assert isinstance(inner, AnthropicChatAdapter) and inner.default_model == "claude-opus-5-5"
    assert inner.base_url == "https://api.anthropic.com"


def test_setup_menu_lists_anthropic() -> None:
    assert "anthropic" in menu._KEY_PROVIDERS
    # /models を取りに行かない（Bearer を送らない）。一覧は手入力
    assert menu._remote_chat_models("anthropic") == []
