"""設定・認証の段階的な解決（ADR-04 / 実装設計書 §5.5・§24）。

① 引数 → ② ``OMNI_<PROVIDER>_<KEY>`` → ③ 各社の標準環境変数
→ ④ 秘密ファイル (secrets.toml) → ⑤ 設定ファイル (config.toml)
``.env`` は SDK からは読まない。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

__all__ = ["ConfigSource", "ResolvedSetting", "load_config_file", "resolve_setting"]

ConfigSource = Literal[
    "argument", "omni_env", "provider_env", "secrets_file", "config_file", "default", "unset"
]

# ③ 各社の標準環境変数（左から優先）
PROVIDER_ENV_VARS: dict[tuple[str, str], tuple[str, ...]] = {
    ("gemini", "api_key"): ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ("ollama", "base_url"): ("OLLAMA_HOST",),
    ("ollama-cloud", "api_key"): ("OLLAMA_API_KEY",),
    ("xai", "api_key"): ("XAI_API_KEY",),
    ("agnes", "api_key"): ("AGNES_API_KEY",),
    ("openrouter", "api_key"): ("OPENROUTER_API_KEY",),
}

_SECRET_KEYS = {"api_key", "token", "secret"}


@dataclass(frozen=True)
class ResolvedSetting:
    value: str | None
    source: ConfigSource
    name: str

    def describe(self) -> str:
        shown = self.value
        if shown and any(k in self.name for k in _SECRET_KEYS):
            shown = f"{shown[:4]}…({len(shown)} chars)"
        return f"{self.name} = {shown!r} (from {self.source})"


def default_config_path() -> Path:
    from .settings import config_path

    return config_path()


def load_config_file(path: Path | None = None) -> dict[str, Any]:
    target = path or default_config_path()
    try:
        with target.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}


def resolve_setting(
    provider: str,
    key: str,
    explicit: str | None = None,
    *,
    default: str | None = None,
    environ: dict[str, str] | None = None,
    config: dict[str, Any] | None = None,
) -> ResolvedSetting:
    env = os.environ if environ is None else environ
    name = f"{provider}.{key}"
    if explicit:
        return ResolvedSetting(explicit, "argument", name)
    # "ollama-cloud" → OMNI_OLLAMA_CLOUD_API_KEY（環境変数名にハイフンは使えない）
    omni_var = f"OMNI_{provider.upper().replace('-', '_')}_{key.upper()}"
    if env.get(omni_var):
        return ResolvedSetting(env[omni_var], "omni_env", name)
    for var in PROVIDER_ENV_VARS.get((provider, key), ()):
        if env.get(var):
            return ResolvedSetting(env[var], "provider_env", name)
    if config is None:
        from .settings import load_secrets

        sec_section = load_secrets().get(provider)
        if isinstance(sec_section, dict) and sec_section.get(key):
            return ResolvedSetting(str(sec_section[key]), "secrets_file", name)
    cfg = load_config_file() if config is None else config
    section = cfg.get(provider)
    if isinstance(section, dict) and section.get(key):
        return ResolvedSetting(str(section[key]), "config_file", name)
    if default is not None:
        return ResolvedSetting(default, "default", name)
    return ResolvedSetting(None, "unset", name)
