"""プロバイダ別アダプター（遅延 import）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gemini import GeminiChatAdapter
    from .ollama import OllamaChatAdapter

__all__ = ["GeminiChatAdapter", "OllamaChatAdapter"]


def __getattr__(name: str) -> Any:
    if name == "OllamaChatAdapter":
        from .ollama import OllamaChatAdapter

        return OllamaChatAdapter
    if name == "GeminiChatAdapter":
        from .gemini import GeminiChatAdapter

        return GeminiChatAdapter
    raise AttributeError(name)
