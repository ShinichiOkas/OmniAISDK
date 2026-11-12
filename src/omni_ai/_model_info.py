"""モデルの情報（文脈長ほか）を共通で取るための小さな API。

- :class:`ModelInfo`: 1 モデル分の情報
- :func:`static_context_window`: 予備の静的表（有名モデルの既定値）
- :func:`parse_ollama_show`: Ollama ``/api/show`` の応答から文脈長を拾う
- :func:`parse_openrouter_models`: OpenRouter ``/models`` の応答から 1 覧と文脈長を拾う

アダプタ側は自前の HTTP で情報を取り、ここの関数で解釈する。実際の ``model_info()`` / ``list_models()``
は :class:`ChatCapability` の拡張点として個別アダプタが実装する（本モジュールは解釈の共通化だけ）。
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "STATIC_CONTEXT_WINDOWS",
    "ModelInfo",
    "parse_ollama_show",
    "parse_ollama_tags",
    "parse_openrouter_models",
    "static_context_window",
]


@dataclass(frozen=True)
class ModelInfo:
    """モデル 1 本分の共通情報。"""

    name: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    pricing: dict[str, float] = field(default_factory=dict)  # { "input": $/1M, "output": $/1M }
    raw: dict[str, Any] = field(default_factory=dict)        # プロバイダ固有の生応答


# ------------- 予備の静的表（接続できない・取れない場合の目安） -------------

# 既知の有名モデルの文脈長（2026-11 時点の目安。古くなる前提で、取れないときの最後の砦）
STATIC_CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic（Claude 5 系は 1M。旧表の 4 系は別票で見直す）
    "claude-fable-5-1": 1_000_000,
    "claude-opus-5-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-haiku-5-5": 1_000_000,
    "claude-opus-4-8": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    # OpenAI
    "gpt-5.5": 272_000,
    "gpt-5": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    # Google
    "gemini-3.1-pro": 1_000_000,
    "gemini-3.1-flash": 1_000_000,
    "gemini-3.1-flash-lite": 1_000_000,
    "gemini-3.5-flash": 1_000_000,
    # Local
    "gemma4:31b-cloud": 131_072,
    "gemma4:31b": 131_072,
    "gpt-oss:120b": 131_072,
    "gpt-oss:20b": 131_072,
}


def static_context_window(model: str) -> int | None:
    """モデル名から静的表の文脈長を返す（無ければ None）。別名・数字の `-cloud` 違いも拾う。"""
    if model in STATIC_CONTEXT_WINDOWS:
        return STATIC_CONTEXT_WINDOWS[model]
    # "-cloud" のあり/なし
    stripped = model.replace("-cloud", "")
    if stripped in STATIC_CONTEXT_WINDOWS:
        return STATIC_CONTEXT_WINDOWS[stripped]
    # バージョン付き別名（例: claude-opus-4-8-20260801）
    for key in STATIC_CONTEXT_WINDOWS:
        if model.startswith(key + "-"):
            return STATIC_CONTEXT_WINDOWS[key]
    return None


# ------------- Ollama: /api/show -------------

_OLLAMA_CTX_KEYS = (
    "llama.context_length",
    "gemma.context_length",
    "gemma2.context_length",
    "gemma3.context_length",
    "gemma4.context_length",
    "qwen2.context_length",
    "qwen3.context_length",
    "mistral.context_length",
    "phi3.context_length",
)

_OLLAMA_CTX_RE = re.compile(r"[a-z_]+\.context_length\s*=\s*(\d+)")


def parse_ollama_show(data: dict[str, Any], *, name: str) -> ModelInfo:
    """Ollama の ``/api/show`` の応答から :class:`ModelInfo` を組み立てる。

    - ``model_info`` の中にアーキテクチャごとのキー（例: ``llama.context_length``）がある
    - 取れないときは ``parameters`` の文字列から ``*.context_length = <n>`` を拾う
    """
    ctx: int | None = None
    mi = data.get("model_info") or {}
    if isinstance(mi, dict):
        for key in _OLLAMA_CTX_KEYS:
            v = mi.get(key)
            if isinstance(v, int) and v > 0:
                ctx = int(v)
                break
        if ctx is None:
            # ``<family>.context_length`` 形式のキーを全部走査
            for key, v in mi.items():
                if isinstance(key, str) and key.endswith(".context_length") and isinstance(v, int):
                    ctx = int(v)
                    break
    if ctx is None:
        params = data.get("parameters")
        if isinstance(params, str):
            m = _OLLAMA_CTX_RE.search(params)
            if m:
                ctx = int(m.group(1))
    capabilities = data.get("capabilities") or []
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    if isinstance(capabilities, list):
        if "tools" in capabilities:
            supports_tools = True
        if "vision" in capabilities:
            supports_vision = True
    return ModelInfo(
        name=name,
        context_window=ctx,
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        raw=dict(data),
    )


def parse_ollama_tags(data: dict[str, Any]) -> list[str]:
    """Ollama の ``/api/tags`` の応答からモデル名の一覧を返す。"""
    models = data.get("models") or []
    if not isinstance(models, list):
        return []
    out = []
    for m in models:
        if isinstance(m, dict) and isinstance(m.get("name"), str):
            out.append(m["name"])
    return out


# ------------- OpenRouter: /models -------------


def parse_openrouter_models(data: dict[str, Any]) -> dict[str, ModelInfo]:
    """OpenRouter の ``/models`` の応答を名前 → :class:`ModelInfo` に写す。"""
    items = data.get("data") or []
    if not isinstance(items, list):
        return {}
    out: dict[str, ModelInfo] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("id") or item.get("name")
        if not isinstance(name, str):
            continue
        ctx = item.get("context_length")
        if not isinstance(ctx, int):
            ctx = None
        max_out = (item.get("top_provider") or {}).get("max_completion_tokens")
        if not isinstance(max_out, int):
            max_out = None
        pricing = {}
        p = item.get("pricing") or {}
        for key in ("prompt", "completion"):
            v = p.get(key)
            with contextlib.suppress(TypeError, ValueError):
                pricing["input" if key == "prompt" else "output"] = float(v) if v is not None else 0.0
        architecture = item.get("architecture") or {}
        modalities = architecture.get("input_modalities") or []
        supports_vision = "image" in modalities if isinstance(modalities, list) else None
        supports_tools = bool(item.get("supported_parameters") and "tools" in (item.get("supported_parameters") or []))
        out[name] = ModelInfo(
            name=name,
            context_window=ctx,
            max_output_tokens=max_out,
            supports_tools=supports_tools or None,
            supports_vision=supports_vision,
            pricing=pricing,
            raw=dict(item),
        )
    return out
