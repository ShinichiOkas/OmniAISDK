"""環境の診断（omni-ai doctor）。足りないものと、その直し方を一覧にする。"""

from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .._version import __version__
from ..compat import PlatformCompat
from ..settings import OmniConfig, config_path, secrets_path, validate_files

__all__ = ["EXTRAS", "Check", "run_checks"]

Status = Literal["ok", "warn", "fail", "skip"]

# extras 名 → 読み込めるべきモジュール
EXTRAS: dict[str, list[str]] = {
    "local": ["fastembed", "onnxruntime"],
    "search": ["ddgs"],
    "docs": ["pypdf", "markitdown"],
    "mcp": ["mcp"],
    "audio": ["edge_tts", "faster_whisper"],
}

MIN_OLLAMA_FOR_JUDGE = (0, 35, 0)


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    hint: str = ""


def _version_tuple(v: str) -> tuple[int, ...]:
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def check_python() -> Check:
    ok = sys.version_info >= (3, 11)
    return Check(
        "python",
        "ok" if ok else "fail",
        f"Python {platform.python_version()} on {platform.system()} {platform.machine()}"
        f" / omni-ai-sdk {__version__}",
        "" if ok else "Python 3.11 or later is required",
    )


def check_config() -> tuple[list[Check], OmniConfig | None]:
    out: list[Check] = []
    cfg_file, sec_file = config_path(), secrets_path()
    out.append(
        Check(
            "config file",
            "ok" if cfg_file.exists() else "warn",
            str(cfg_file),
            "" if cfg_file.exists() else "run `omni-ai setup` to create it",
        )
    )
    if sec_file.exists():
        mode_ok = PlatformCompat.is_windows() or (sec_file.stat().st_mode & 0o077) == 0
        out.append(
            Check(
                "secrets file",
                "ok" if mode_ok else "warn",
                str(sec_file),
                "" if mode_ok else f"permissions are too open; run: chmod 600 {sec_file}",
            )
        )
    cfg, issues = validate_files()
    if not issues:
        out.append(Check("config validation", "ok", "no problems"))
    for i in issues:
        out.append(
            Check("config validation", "fail" if i.level == "error" else "warn", f"{i.where}: {i.message}")
        )
    return out, cfg


def check_extras() -> list[Check]:
    out = []
    for extra, modules in EXTRAS.items():
        missing = [m for m in modules if not _installed(m)]
        out.append(
            Check(
                f"extras [{extra}]",
                "ok" if not missing else "skip",
                "installed" if not missing else f"not installed ({', '.join(missing)})",
                "" if not missing else f'pip install "omni-ai-sdk[{extra}]"  (or: omni-ai setup)',
            )
        )
    return out


def _ollama_needed(cfg: OmniConfig | None) -> dict[str, str]:
    """設定が使う Ollama のモデル → 用途。"""
    need: dict[str, str] = {}
    if cfg is None:
        return need
    c = cfg.client
    if c.chat and "ollama" in c.chat.providers and c.chat.models.get("ollama"):
        need[c.chat.models["ollama"]] = "chat"
    if c.embedding and c.embedding.provider == "ollama" and c.embedding.model:
        need[c.embedding.model] = "embedding"
    if c.judge:
        need[c.judge.model] = "judge"
    return need


def check_ollama(cfg: OmniConfig | None, client: httpx.Client | None = None) -> list[Check]:
    base = (
        cfg.ollama.base_url if cfg and cfg.ollama and cfg.ollama.base_url else None
    ) or PlatformCompat.resolve_ollama_base_url()
    need = _ollama_needed(cfg)
    http = client or httpx.Client(timeout=5)
    try:
        version = http.get(f"{base}/api/version").json().get("version", "?")
        tags = {m["name"] for m in http.get(f"{base}/api/tags").json().get("models", [])}
    except (httpx.HTTPError, ValueError) as exc:
        status: Status = "fail" if need else "skip"
        return [
            Check(
                "ollama",
                status,
                f"not reachable at {base} ({type(exc).__name__})",
                "install / start Ollama: https://ollama.com/download",
            )
        ]
    out = [Check("ollama", "ok", f"{version} at {base}")]
    if cfg and cfg.client.judge and _version_tuple(version) < MIN_OLLAMA_FOR_JUDGE:
        out.append(
            Check(
                "ollama version",
                "fail",
                f"{version} < 0.35.0 (decision models for judge)",
                "update Ollama to 0.35.0 or later",
            )
        )
    for model, use in need.items():
        present = model in tags or f"{model}:latest" in tags
        out.append(
            Check(
                f"ollama model ({use})",
                "ok" if present else "fail",
                model,
                "" if present else f"omni-ai models pull ollama:{model}",
            )
        )
    return out


def check_gemini(cfg: OmniConfig | None) -> list[Check]:
    from ..config import resolve_setting

    used = bool(
        cfg
        and (
            (cfg.client.chat and "gemini" in cfg.client.chat.providers)
            or (cfg.client.embedding and cfg.client.embedding.provider == "gemini")
        )
    )
    key = resolve_setting("gemini", "api_key")
    if key.value:
        return [Check("gemini api key", "ok", f"found ({key.source})")]
    return [
        Check(
            "gemini api key",
            "fail" if used else "skip",
            "not set",
            "omni-ai setup (saved to secrets.toml) or set GEMINI_API_KEY",
        )
    ]


def check_chat_providers(cfg: OmniConfig | None, client: httpx.Client | None = None) -> list[Check]:
    """Chat に並べた、Gemini・Ollama 以外のプロバイダーのキーと接続先。"""
    from ..config import resolve_setting
    from ..settings import CHAT_PROVIDERS

    if not (cfg and cfg.client.chat):
        return []
    out: list[Check] = []
    for name in cfg.client.chat.providers:
        if name in ("gemini", "ollama"):
            continue  # check_gemini / check_ollama が見る
        info = CHAT_PROVIDERS[name]
        key = resolve_setting(name, "api_key")
        if info.key_required:
            env = info.key_env[0] if info.key_env else f"OMNI_{name.upper().replace('-', '_')}_API_KEY"
            out.append(
                Check(f"{name} api key", "ok", f"found ({key.source})")
                if key.value
                else Check(
                    f"{name} api key",
                    "fail",
                    "not set",
                    f"omni-ai setup (saved to secrets.toml) or set {env}",
                )
            )
        if name == "openai-compatible":
            out.append(_check_openai_compatible(cfg, key.value, client))
    return out


def _check_openai_compatible(cfg: OmniConfig, key: str | None, client: httpx.Client | None) -> Check:
    from ..config import resolve_setting
    from ..providers.openai_compatible import normalize_base_url

    conn = cfg.connection("openai-compatible")
    url = resolve_setting("openai-compatible", "base_url", conn.base_url if conn else None).value
    if not url:
        return Check(
            "openai-compatible", "fail", "no base_url", "set [openai-compatible] base_url in config.toml"
        )
    base = normalize_base_url(url)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        r = (client or httpx).get(f"{base}/models", headers=headers, timeout=10)
        r.raise_for_status()
        n = len(r.json().get("data") or [])
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        return Check("openai-compatible", "fail", f"{base}: {exc}", "start the server (e.g. llama-server)")
    return Check("openai-compatible", "ok", f"{base} ({n} model(s))")


def check_docker(cfg: OmniConfig | None, runner: Callable[..., Any] = subprocess.run) -> list[Check]:
    used = bool(cfg and cfg.client.sandbox)
    try:
        docker, env = PlatformCompat.resolve_docker()
    except FileNotFoundError:
        return [
            Check(
                "docker",
                "fail" if used else "skip",
                "docker CLI not found",
                "install Docker Desktop (or Docker Engine on Linux)",
            )
        ]
    try:
        p = runner(
            [docker, "version", "--format", "{{.Server.Version}}"], capture_output=True, env=env, timeout=15
        )
    except subprocess.TimeoutExpired:
        return [
            Check(
                "docker",
                "fail" if used else "warn",
                "daemon did not answer in 15s",
                "start Docker Desktop (accept its license screen if shown)",
            )
        ]
    server = p.stdout.decode(errors="replace").strip()
    if p.returncode != 0 or not server:
        return [
            Check(
                "docker",
                "fail" if used else "warn",
                "daemon not available",
                "start Docker Desktop / dockerd; on Linux check the 'docker' group",
            )
        ]
    out = [Check("docker", "ok", f"server {server} ({docker})")]
    if used and cfg and cfg.client.sandbox:
        from ..providers.docker import DEFAULT_IMAGE

        image = cfg.client.sandbox.image or DEFAULT_IMAGE
        q = runner([docker, "image", "inspect", image], capture_output=True, env=env, timeout=15)
        out.append(
            Check(
                "docker image",
                "ok" if q.returncode == 0 else "fail",
                image,
                "" if q.returncode == 0 else f"omni-ai models pull docker:{image}",
            )
        )
    return out


def run_checks() -> list[Check]:
    checks = [check_python()]
    cfg_checks, cfg = check_config()
    checks += cfg_checks
    checks += check_extras()
    checks += check_gemini(cfg)
    checks += check_ollama(cfg)
    checks += check_chat_providers(cfg)
    checks += check_docker(cfg)
    return checks
