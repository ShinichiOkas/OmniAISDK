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

from ..settings import CHAT_PROVIDERS, load_config, load_secrets, save_config, save_secrets, validate_files

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


# 鍵を入れるプロバイダー（メニューの並び順。Gemini を先頭に固定）
_KEY_PROVIDERS = ["gemini", "ollama-cloud", "xai", "agnes", "openrouter", "openai-compatible", "anthropic"]


def _save_key(io: IO, name: str) -> None:
    label = CHAT_PROVIDERS[name].label
    key = io.secret(f"{label} の API キー（入力は表示されません。空なら取りやめ） > ").strip()
    if not key:
        io.print("取りやめました")
        return
    secrets = load_secrets()
    secrets.setdefault(name, {})["api_key"] = key
    path = save_secrets(secrets)
    io.print(f"保存しました（本人だけが読めるファイル）: {path}")


def menu_providers(io: IO) -> None:
    keyed = [n for n in _KEY_PROVIDERS if n != "gemini"]
    options = ["Gemini の API キー", "Ollama の接続先"]
    options += [f"{CHAT_PROVIDERS[n].label} の API キー" for n in keyed]
    options += ["OpenAI 互換サーバーの接続先"]
    choice = _choose(io, "プロバイダーの設定", options)
    if choice is None:
        return
    index = options.index(choice)
    if index == 0:
        _save_key(io, "gemini")
        return
    if 2 <= index < 2 + len(keyed):
        _save_key(io, keyed[index - 2])
        return
    raw = load_config()
    if choice.startswith("OpenAI"):
        current = (raw.get("openai-compatible") or {}).get("base_url", "")
        url = (
            io.ask(f"OpenAI 互換サーバーの URL（例: http://127.0.0.1:8080） [{current}] > ").strip()
            or current
        )
        if not url:
            io.print("取りやめました")
            return
        raw.setdefault("openai-compatible", {})["base_url"] = url
        _save_and_validate(io, raw)
        return
    from ..compat import PlatformCompat

    current = PlatformCompat.resolve_ollama_base_url()
    url = io.ask(f"Ollama の接続先 [{current}] > ").strip() or current
    raw.setdefault("ollama", {})["base_url"] = url
    _save_and_validate(io, raw)
    io.print(f"Ollama が入っていなければ: {OLLAMA_INSTALL}")


def _remote_chat_models(name: str) -> list[str]:
    """クラウド・OpenAI 互換サーバーのモデル一覧（取れなければ空。多すぎるときも空＝手入力）。"""
    import httpx

    from ..config import resolve_setting

    key = resolve_setting(name, "api_key").value
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        if name == "ollama-cloud":
            from ..providers.ollama import OLLAMA_CLOUD_BASE_URL

            base = (
                resolve_setting(name, "base_url", default=OLLAMA_CLOUD_BASE_URL).value
                or OLLAMA_CLOUD_BASE_URL
            )
            data = httpx.get(f"{base.rstrip('/')}/api/tags", headers=headers, timeout=10).json()
            names = [m.get("name", "") for m in data.get("models") or []]
        elif name == "anthropic":
            # Messages API の /v1/models は x-api-key 認証。一覧は手入力でよいので取りに行かない
            return []
        else:
            from ..providers import openai_compatible as oc

            default = {
                "xai": oc.XAIChatAdapter.default_base_url,
                "agnes": oc.AgnesChatAdapter.default_base_url,
                "openrouter": oc.OpenRouterChatAdapter.default_base_url,
            }.get(name)
            url = resolve_setting(name, "base_url", default=default).value
            if not url:
                return []
            base = oc.normalize_base_url(url) if name == "openai-compatible" else url.rstrip("/")
            data = httpx.get(f"{base}/models", headers=headers, timeout=10).json()
            names = [m.get("id", "") for m in data.get("data") or []]
    except (httpx.HTTPError, ValueError, AttributeError):
        return []
    names = sorted(n for n in names if n)
    return names if len(names) <= 30 else []


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


def _ask_chat_model(io: IO, name: str) -> str:
    info = CHAT_PROVIDERS[name]
    if name == "gemini":
        return io.ask("Gemini のモデル [gemini-3.1-flash-lite] > ").strip()
    names = _ollama_model_names("completion") if name == "ollama" else _remote_chat_models(name)
    if names:
        return _choose(io, f"{info.label} のモデル（対話できるもの）", names, allow_skip=False) or ""
    return io.ask(f"{info.label} のモデル名（例: {info.model_example}） > ").strip()


def menu_chat(io: IO) -> None:
    raw = load_config()
    order: list[str] = []
    models: dict[str, str] = {}
    while True:
        remaining = [p for p in CHAT_PROVIDERS if p not in order]
        if not remaining:
            break
        pick = _choose(
            io,
            f"Chat のプロバイダー（{len(order) + 1} 番目。先に選んだものから使い、失敗したら次へ。0 で決定）",
            remaining,
        )
        if pick is None:
            break
        order.append(pick)
        if m := _ask_chat_model(io, pick):
            models[pick] = m
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
