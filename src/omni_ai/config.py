"""設定・認証の 4 段階解決（ADR-04 / 実装設計書 §5.5）。

① 引数 → ② ``OMNI_<PROVIDER>_<KEY>`` → ③ 各社の標準環境変数 → ④ 設定ファイル (config.toml)
``.env`` は SDK からは読まない。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .compat import PlatformCompat

__all__ = ["ConfigSource", "ResolvedSetting", "load_config_file", "resolve_setting"]

ConfigSource = Literal["argument", "omni_env", "provider_env", "config_file", "default", "unset"]

# ③ 各社の標準環境変数（左から優先）
PROVIDER_ENV_VARS: dict[tuple[str, str], tuple[str, ...]] = {
    ("gemini", "api_key"): ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ("ollama", "base_url"): ("OLLAMA_HOST",),
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
    return PlatformCompat.get_config_dir(create=False) / "config.toml"


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
    omni_var = f"OMNI_{provider.upper()}_{key.upper()}"
    if env.get(omni_var):
        return ResolvedSetting(env[omni_var], "omni_env", name)
    for var in PROVIDER_ENV_VARS.get((provider, key), ()):
        if env.get(var):
            return ResolvedSetting(env[var], "provider_env", name)
    cfg = load_config_file() if config is None else config
    section = cfg.get(provider)
    if isinstance(section, dict) and section.get(key):
        return ResolvedSetting(str(section[key]), "config_file", name)
    if default is not None:
        return ResolvedSetting(default, "default", name)
    return ResolvedSetting(None, "unset", name)
