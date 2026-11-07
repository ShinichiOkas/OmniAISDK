"""プロバイダ別アダプター（遅延 import）。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ddgs import DDGSSearchAdapter
    from .fastembed import FastEmbedEmbeddingAdapter
    from .fastembed.rerank import FastEmbedRerankAdapter
    from .gemini import GeminiChatAdapter
    from .gemini.embedding import GeminiEmbeddingAdapter
    from .ollama import OllamaChatAdapter
    from .ollama.embedding import OllamaEmbeddingAdapter

_LAZY = {
    "DDGSSearchAdapter": ".ddgs",
    "GeminiChatAdapter": ".gemini",
    "GeminiEmbeddingAdapter": ".gemini.embedding",
    "FastEmbedEmbeddingAdapter": ".fastembed",
    "FastEmbedRerankAdapter": ".fastembed.rerank",
    "OllamaChatAdapter": ".ollama",
    "OllamaEmbeddingAdapter": ".ollama.embedding",
}

__all__ = [
    "DDGSSearchAdapter",
    "FastEmbedEmbeddingAdapter",
    "FastEmbedRerankAdapter",
    "GeminiChatAdapter",
    "GeminiEmbeddingAdapter",
    "OllamaChatAdapter",
    "OllamaEmbeddingAdapter",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(name)
