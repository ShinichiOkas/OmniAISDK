"""プロバイダ別アダプター（遅延 import）。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._audio_base import AudioRouter
    from ._doc_base import MimeRoutingDocumentParser
    from .ddgs import DDGSSearchAdapter
    from .docker import DockerSandboxAdapter
    from .edge_tts import EdgeTTSAudioAdapter
    from .fastembed import FastEmbedEmbeddingAdapter
    from .fastembed.rerank import FastEmbedRerankAdapter
    from .faster_whisper import FasterWhisperAudioAdapter
    from .gemini import GeminiChatAdapter
    from .gemini.embedding import GeminiEmbeddingAdapter
    from .markitdown import MarkItDownDocumentParser
    from .mcp import MCPClientAdapter
    from .ollama import OllamaChatAdapter
    from .ollama.embedding import OllamaEmbeddingAdapter
    from .ollama.judge import OllamaJudgeAdapter
    from .pypdf import PyPDFDocumentParser

_LAZY = {
    "DockerSandboxAdapter": ".docker",
    "AudioRouter": "._audio_base",
    "EdgeTTSAudioAdapter": ".edge_tts",
    "FasterWhisperAudioAdapter": ".faster_whisper",
    "MCPClientAdapter": ".mcp",
    "MarkItDownDocumentParser": ".markitdown",
    "MimeRoutingDocumentParser": "._doc_base",
    "PyPDFDocumentParser": ".pypdf",
    "DDGSSearchAdapter": ".ddgs",
    "GeminiChatAdapter": ".gemini",
    "GeminiEmbeddingAdapter": ".gemini.embedding",
    "FastEmbedEmbeddingAdapter": ".fastembed",
    "FastEmbedRerankAdapter": ".fastembed.rerank",
    "OllamaChatAdapter": ".ollama",
    "OllamaEmbeddingAdapter": ".ollama.embedding",
    "OllamaJudgeAdapter": ".ollama.judge",
}

__all__ = [
    "AudioRouter",
    "DDGSSearchAdapter",
    "DockerSandboxAdapter",
    "EdgeTTSAudioAdapter",
    "FastEmbedEmbeddingAdapter",
    "FastEmbedRerankAdapter",
    "FasterWhisperAudioAdapter",
    "GeminiChatAdapter",
    "GeminiEmbeddingAdapter",
    "MCPClientAdapter",
    "MarkItDownDocumentParser",
    "MimeRoutingDocumentParser",
    "OllamaChatAdapter",
    "OllamaEmbeddingAdapter",
    "OllamaJudgeAdapter",
    "PyPDFDocumentParser",
    "default_document_parser",
]


def default_document_parser() -> MimeRoutingDocumentParser:
    """PDF は pypdf、Office / RTF は markitdown に振り分ける既定の構成（extras ``[docs]``）。"""
    from ._doc_base import MimeRoutingDocumentParser
    from .markitdown import MarkItDownDocumentParser
    from .pypdf import PyPDFDocumentParser

    return MimeRoutingDocumentParser([PyPDFDocumentParser(), MarkItDownDocumentParser()])


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        return getattr(import_module(_LAZY[name], __name__), name)
    raise AttributeError(name)
