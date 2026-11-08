"""メニュー方式のセットアップ（omni-ai を引数なしで起動したとき）。

設定ファイル（config.toml）と秘密ファイル（secrets.toml）を、対話で作り・直す。
Python 側のもの（extras・モデル）は、内容と容量を表示して確認を取ってから入れる。
Ollama・Docker の本体は案内だけ（自動では入れない）。
"""

from __future__ import annotations

import importlib.metadata
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..settings import load_config, load_secrets, save_config, save_secrets, validate_files

if TYPE_CHECKING:
    from . import IO

__all__ = ["extra_requirements", "run_setup"]

CAPABILITIES: dict[str, tuple[str, list[str]]] = {
    # 機能 → (説明, 選べるプロバイダー)
    "embedding": ("埋め込み", ["fastembed", "gemini", "ollama"]),
    "rerank": ("再順位付け", ["fastembed"]),
    "search": ("Web 検索", ["ddgs"]),
    "doc_parser": ("文書解析", ["default"]),
    "judge": ("判定（Ollama の decision モデル）", ["ollama"]),
    "audio": ("音声（edge-tts / faster-whisper）", ["default"]),
    "sandbox": ("コード実行（Docker）", ["docker"]),
}
EXTRA_FOR: dict[str, str] = {
    "fastembed": "local", "ddgs": "search", "default:doc_parser": "docs", "default:audio": "audio",
}  # fmt: skip
OLLAMA_INSTALL = "https://ollama.com/download"
DOCKER_INSTALL = "https://docs.docker.com/get-started/get-docker/"


def _choose(io: IO, title: str, options: list[str], *, allow_skip: bool = True) -> str | None:
    io.print(title)
    for i, o in enumerate(options, 1):
        io.print(f"  {i}) {o}")
    if allow_skip:
        io.print("  0) 使わない／戻る")
    while True:
        ans = io.ask("番号 > ").strip()
        if allow_skip and ans in ("0", ""):
            return None
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1]
        io.print("番号で選んでください")


def _save_and_validate(io: IO, raw: dict[str, Any]) -> None:
    path = save_config(raw)
    io.print(f"保存しました: {path}")
    _, issues = validate_files()
    for i in issues:
        io.print(f"  {i}")
    if not issues:
        io.print("  設定に問題はありません")


# ----------------------------------------------------------------------------- 各メニュー


def menu_providers(io: IO) -> None:
    choice = _choose(io, "プロバイダーの設定", ["Gemini の API キー", "Ollama の接続先"])
    if choice is None:
        return
    if choice.startswith("Gemini"):
        key = io.secret("Gemini の API キー（入力は表示されません。空なら取りやめ） > ").strip()
        if not key:
            io.print("取りやめました")
            return
        secrets = load_secrets()
        secrets.setdefault("gemini", {})["api_key"] = key
        path = save_secrets(secrets)
        io.print(f"保存しました（本人だけが読めるファイル）: {path}")
        return
    from ..compat import PlatformCompat

    current = PlatformCompat.resolve_ollama_base_url()
    url = io.ask(f"Ollama の接続先 [{current}] > ").strip() or current
    raw = load_config()
    raw.setdefault("ollama", {})["base_url"] = url
    _save_and_validate(io, raw)
    io.print(f"Ollama が入っていなければ: {OLLAMA_INSTALL}")


def _ollama_model_names(capability: str | None = None) -> list[str]:
    """Ollama のモデル名。capability（completion / embedding / decision）を指定すると、それを持つものだけ。"""
    import httpx

    from ..compat import PlatformCompat
    from .models import ollama_models

    names = [m.get("name", "") for m in ollama_models() if m.get("name")]
    if capability is None:
        return names
    base = PlatformCompat.resolve_ollama_base_url()
    out = []
    for n in names:
        try:
            caps = (
                httpx.post(f"{base}/api/show", json={"model": n}, timeout=10).json().get("capabilities") or []
            )
        except (httpx.HTTPError, ValueError):
            continue
        if capability in caps:
            out.append(n)
    return out


def menu_chat(io: IO) -> None:
    raw = load_config()
    order: list[str] = []
    models: dict[str, str] = {}
    while True:
        remaining = [p for p in ("gemini", "ollama") if p not in order]
        if not remaining:
            break
        pick = _choose(
            io,
            f"Chat のプロバイダー（{len(order) + 1} 番目。先に選んだものから使い、失敗したら次へ）",
            remaining,
        )
        if pick is None:
            break
        order.append(pick)
        if pick == "ollama":
            names = _ollama_model_names("completion")
            if names:
                m = _choose(io, "Ollama のモデル（対話できるもの）", names, allow_skip=False)
            else:
                m = io.ask("Ollama のモデル名（例: gemma4:31b-cloud） > ").strip()
            if m:
                models["ollama"] = m
        else:
            m = io.ask("Gemini のモデル [gemini-3.1-flash-lite] > ").strip()
            if m:
                models["gemini"] = m
    if not order:
        io.print("変更しませんでした")
        return
    section: dict[str, Any] = {"providers": order}
    if models:
        section["models"] = models
    raw.setdefault("client", {})["chat"] = section
    _save_and_validate(io, raw)


def menu_capability(io: IO) -> None:
    labels = ["chat（対話）"] + [f"{k}（{v[0]}）" for k, v in CAPABILITIES.items()]
    pick = _choose(io, "どの機能を設定しますか", labels)
    if pick is None:
        return
    name = pick.split("（")[0]
    if name == "chat":
        menu_chat(io)
        return
    desc, providers = CAPABILITIES[name]
    prov = _choose(io, f"{desc} のプロバイダー", providers)
    raw = load_config()
    client = raw.setdefault("client", {})
    if prov is None:
        if client.pop(name, None) is not None:
            _save_and_validate(io, raw)
        return
    section: dict[str, Any] = {}
    if name in ("embedding", "rerank", "search", "sandbox", "judge"):
        section["provider"] = prov
    if name == "doc_parser":
        section["provider"] = "default"
    if name == "embedding" and prov == "ollama":
        names = _ollama_model_names("embedding")
        section["model"] = (
            _choose(io, "Ollama の埋め込みモデル", names, allow_skip=False)
            if names
            else io.ask("Ollama の埋め込みモデル（例: nomic-embed-text） > ").strip()
        )
    if name == "judge":
        names = _ollama_model_names("decision")
        section["model"] = (
            _choose(io, "decision モデル", names, allow_skip=False)
            if names
            else io.ask("decision モデル（例: tev1:0.8b） > ").strip()
        )
    if name == "audio":
        voice = io.ask("音声合成の声 [ja-JP-NanamiNeural] > ").strip()
        if voice:
            section["voice"] = voice
        size = _choose(
            io, "音声認識のモデル（faster-whisper）", ["medium", "small", "base"], allow_skip=False
        )
        section["whisper_model"] = size
    client[name] = section
    _save_and_validate(io, raw)
    extra = EXTRA_FOR.get(prov) or EXTRA_FOR.get(f"{prov}:{name}")
    if extra:
        io.print(f"この機能には追加パッケージ [{extra}] が必要です（メニュー 4 で入れられます）")
    if name == "sandbox" and not shutil.which("docker"):
        io.print(f"Docker が必要です: {DOCKER_INSTALL}")


def extra_requirements(extra: str) -> list[str]:
    """インストール済みの omni-ai-sdk のメタデータから、extras の依存を取り出す。"""
    out = []
    for req in importlib.metadata.requires("omni-ai-sdk") or []:
        m = re.search(r";\s*extra\s*==\s*['\"]([^'\"]+)['\"]", req)
        if m and m.group(1) == extra:
            out.append(req.split(";")[0].strip())
    return out


def _pip_command() -> list[str]:
    probe = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True)
    if probe.returncode == 0:
        return [sys.executable, "-m", "pip", "install"]
    uv = shutil.which("uv")  # uv で作った仮想環境には pip が無いことがある
    if uv:
        return [uv, "pip", "install", "-p", sys.executable]
    raise RuntimeError("neither pip nor uv is available for this Python")


def menu_extras(io: IO) -> None:
    from .checks import EXTRAS, _installed

    rows = []
    for extra, modules in EXTRAS.items():
        ok = all(_installed(m) for m in modules)
        rows.append(f"{extra:7s} {'導入済み' if ok else '未導入'}  ({', '.join(extra_requirements(extra))})")
    pick = _choose(io, "追加パッケージ（extras）", rows)
    if pick is None:
        return
    extra = pick.split()[0]
    reqs = extra_requirements(extra)
    try:
        cmd = _pip_command() + reqs
    except RuntimeError as exc:
        io.print(str(exc))
        return
    io.print("実行するコマンド: " + " ".join(cmd))
    if not io.confirm("インストールしますか？"):
        io.print("取りやめました")
        return
    p = subprocess.run(cmd)
    io.print("インストールしました" if p.returncode == 0 else f"失敗しました（終了コード {p.returncode}）")


def menu_models(io: IO) -> None:
    from . import cmd_models
    from .models import CATALOG

    raw = load_config().get("client", {})
    suggestions: list[str] = []
    if raw.get("embedding", {}).get("provider") == "fastembed":
        suggestions.append("fastembed-embedding")
    if raw.get("rerank"):
        suggestions.append("fastembed-rerank")
    audio = raw.get("audio")
    if audio is not None and audio.get("transcription", "faster-whisper"):
        suggestions.append(f"whisper-{audio.get('whisper_model', 'medium')}")
    if raw.get("judge", {}).get("model"):
        suggestions.append(f"ollama:{raw['judge']['model']}")
    if raw.get("sandbox") is not None:
        suggestions.append(f"docker:{raw['sandbox'].get('image') or 'python:3.12-slim'}")
    options = (
        ["一覧を見る"]
        + [f"取得: {s}" for s in suggestions]
        + [f"取得: {k}" for k in CATALOG if k not in suggestions]
        + ["名前を指定して取得", "名前を指定して削除"]
    )
    pick = _choose(io, "モデル（設定が使うものを先に並べています）", options)
    if pick is None:
        return
    if pick == "一覧を見る":
        cmd_models(io, "list", None, False)
    elif pick.startswith("取得: "):
        cmd_models(io, "pull", pick.removeprefix("取得: "), False)
    elif pick == "名前を指定して取得":
        cmd_models(io, "pull", io.ask("名前（例: ollama:tev1:0.8b） > ").strip(), False)
    else:
        cmd_models(io, "remove", io.ask("名前 > ").strip(), False)


def run_setup(io: IO) -> int:
    from . import cmd_config, cmd_doctor

    menu: dict[str, Callable[[], object]] = {
        "環境の診断（doctor）": lambda: cmd_doctor(io),
        "プロバイダーの設定（API キー・Ollama の接続先）": lambda: menu_providers(io),
        "機能の構成（chat / embedding / search …）": lambda: menu_capability(io),
        "追加パッケージの導入（extras）": lambda: menu_extras(io),
        "モデルの取得・一覧・削除": lambda: menu_models(io),
        "設定の確認と検証": lambda: (cmd_config(io, "path"), cmd_config(io, "validate")),
    }
    io.print("OmniAI SDK セットアップ")
    while True:
        io.print("")
        for i, label in enumerate(menu, 1):
            io.print(f"  {i}) {label}")
        io.print("  0) 終了")
        ans = io.ask("番号 > ").strip()
        if ans in ("0", "q", ""):
            return 0
        if ans.isdigit() and 1 <= int(ans) <= len(menu):
            try:
                list(menu.values())[int(ans) - 1]()
            except KeyboardInterrupt:
                io.print("\n中断しました")
            except Exception as exc:  # メニューは落とさない
                io.print(f"エラー: {type(exc).__name__}: {exc}")
        else:
            io.print("番号で選んでください")
