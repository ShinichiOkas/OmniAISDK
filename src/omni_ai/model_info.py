"""モデル情報（文脈長など）の共通型と解釈の関数（公開の入口）。

実体は :mod:`omni_ai._model_info`。``_model_info`` からの import も従来どおり動く（同じオブジェクト）。
"""

from __future__ import annotations

from ._model_info import (
    STATIC_CONTEXT_WINDOWS,
    ModelInfo,
    parse_ollama_show,
    parse_ollama_tags,
    parse_openrouter_models,
    static_context_window,
)

__all__ = [
    "STATIC_CONTEXT_WINDOWS",
    "ModelInfo",
    "parse_ollama_show",
    "parse_ollama_tags",
    "parse_openrouter_models",
    "static_context_window",
]
