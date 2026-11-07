"""プロバイダ別アダプター（遅延 import）。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gemini import GeminiChatAdapter
    from .gemini.embedding import GeminiEmbeddingAdapter
    from .local import LocalEmbeddingAdapter
    from .ollama import OllamaChatAdapter
    from .ollama.embedding import OllamaEmbeddingAdapter

_LAZY = {
    "GeminiChatAdapter": ".gemini",
    "GeminiEmbeddingAdapter": ".gemini.embedding",
    "LocalEmbeddingAdapter": ".local",
    "OllamaChatAdapter": ".ollama",
    "OllamaEmbeddingAdapter": ".ollama.embedding",
}

__all__ = [
    "GeminiChatAdapter",
    "GeminiEmbeddingAdapter",
    "LocalEmbeddingAdapter",
    "OllamaChatAdapter",
    "OllamaEmbeddingAdapter",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(name)
