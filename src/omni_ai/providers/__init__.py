"""プロバイダ別アダプター（遅延 import）。"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._audio_base import AudioRouter
    from ._doc_base import MimeRoutingDocumentParser
    from .anthropic import AnthropicChatAdapter
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
    from .ollama import OllamaChatAdapter, OllamaCloudChatAdapter
    from .ollama.embedding import OllamaEmbeddingAdapter
    from .ollama.judge import OllamaJudgeAdapter
    from .openai_compatible import (
        AgnesChatAdapter,
        OpenAICompatibleChatAdapter,
        OpenRouterChatAdapter,
        XAIChatAdapter,
    )
    from .openai_compatible.image import OpenAICompatibleImageAdapter
    from .openai_compatible.search import OpenAICompatibleSearchAdapter
    from .pypdf import PyPDFDocumentParser
    from .web_fetch import HttpxWebFetchAdapter

_LAZY = {
    "AnthropicChatAdapter": ".anthropic",
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
    "OllamaCloudChatAdapter": ".ollama",
    "OpenAICompatibleChatAdapter": ".openai_compatible",
    "OpenAICompatibleImageAdapter": ".openai_compatible.image",
    "OpenAICompatibleSearchAdapter": ".openai_compatible.search",
    "XAIChatAdapter": ".openai_compatible",
    "AgnesChatAdapter": ".openai_compatible",
    "OpenRouterChatAdapter": ".openai_compatible",
    "OllamaEmbeddingAdapter": ".ollama.embedding",
    "OllamaJudgeAdapter": ".ollama.judge",
    "HttpxWebFetchAdapter": ".web_fetch",
}

__all__ = [
    "AgnesChatAdapter",
    "AnthropicChatAdapter",
    "AudioRouter",
    "DDGSSearchAdapter",
    "DockerSandboxAdapter",
    "EdgeTTSAudioAdapter",
    "FastEmbedEmbeddingAdapter",
    "FastEmbedRerankAdapter",
    "FasterWhisperAudioAdapter",
    "GeminiChatAdapter",
    "GeminiEmbeddingAdapter",
    "HttpxWebFetchAdapter",
    "MCPClientAdapter",
    "MarkItDownDocumentParser",
    "MimeRoutingDocumentParser",
    "OllamaChatAdapter",
    "OllamaCloudChatAdapter",
    "OllamaEmbeddingAdapter",
    "OllamaJudgeAdapter",
    "OpenAICompatibleChatAdapter",
    "OpenAICompatibleImageAdapter",
    "OpenAICompatibleSearchAdapter",
    "OpenRouterChatAdapter",
    "PyPDFDocumentParser",
    "XAIChatAdapter",
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
