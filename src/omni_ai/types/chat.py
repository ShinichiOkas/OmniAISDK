"""Chat のメッセージ・応答・ストリームイベント。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import FinishReason, ResponseMeta
from .media import ContentPart, TextPart
from .tools import ToolCall

__all__ = ["ChatMessage", "ChatResponse", "Citation", "Role", "StreamEvent"]


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    role: Role
    content: str | list[ContentPart] = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    # アダプターだけが読み書きする不透明な状態。キーはプロバイダ名 (ADR-11)
    provider_state: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def system(cls, text: str) -> ChatMessage:
        return cls(role=Role.SYSTEM, content=text)

    @classmethod
    def user(cls, content: str | list[ContentPart]) -> ChatMessage:
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, text: str) -> ChatMessage:
        return cls(role=Role.ASSISTANT, content=text)

    @classmethod
    def tool(cls, content: str, *, tool_call_id: str, name: str) -> ChatMessage:
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id, name=name)

    def text(self) -> str:
        """テキスト部分だけを連結して返す。"""
        if isinstance(self.content, str):
            return self.content
        return "\n".join(p.text for p in self.content if isinstance(p, TextPart))


class Citation(BaseModel):
    url: str
    title: str | None = None
    start_index: int | None = None
    end_index: int | None = None


class ChatResponse(BaseModel):
    content: str | None = None
    parsed: Any | None = None
    reasoning: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    finish_reason: FinishReason = "stop"
    meta: ResponseMeta
    # アシスタントメッセージに引き継ぐ不透明な状態（例: Gemini の parts）
    provider_state: dict[str, Any] = Field(default_factory=dict)

    def to_message(self) -> ChatMessage:
        """履歴にそのまま積めるアシスタントメッセージを返す。"""
        return ChatMessage(
            role=Role.ASSISTANT,
            content=self.content or "",
            tool_calls=[tc.model_copy(deep=True) for tc in self.tool_calls] or None,
            provider_state=dict(self.provider_state),
        )


class StreamEvent(BaseModel):
    type: Literal["text_delta", "reasoning_delta", "tool_call", "citation", "message_complete"]
    delta_text: str = ""
    tool_call: ToolCall | None = None
    citation: Citation | None = None
    finish_reason: FinishReason | None = None
    response: ChatResponse | None = None
    meta: ResponseMeta | None = None
