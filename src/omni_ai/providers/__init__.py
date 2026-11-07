"""プロバイダ別アダプター（遅延 import）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ollama import OllamaChatAdapter

__all__ = ["OllamaChatAdapter"]


def __getattr__(name: str) -> Any:
    if name == "OllamaChatAdapter":
        from .ollama import OllamaChatAdapter

        return OllamaChatAdapter
    raise AttributeError(name)
