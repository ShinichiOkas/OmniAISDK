"""全 10 領域の Sync / Async 抽象インタフェース（仕様書 v3.1 §5）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator, Sequence
from types import TracebackType
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel

from .types import (
    AudioSpeechResponse,
    AudioTranscriptionResponse,
    BuiltinTool,
    ChatMessage,
    ChatResponse,
    EmbeddingResponse,
    ImageResponse,
    JudgeQuestion,
    JudgeResponse,
    MediaSource,
    ParsedDocument,
    RerankResponse,
    SandboxExecutionResult,
    SearchResponse,
    StreamEvent,
    ToolDefinition,
    ToolSpec,
)

__all__ = [
    "AudioCapability",
    "Capability",
    "ChatCapability",
    "DocumentParserCapability",
    "EmbeddingCapability",
    "EmbeddingTask",
    "ImageCapability",
    "JudgeCapability",
    "MCPCapability",
    "RerankCapability",
    "SandboxCapability",
    "SearchCapability",
    "ToolChoice",
]

ToolChoice = Literal["auto", "none", "required"] | str


class Capability(ABC):
    """全 Capability の共通基底。"""

    provider_name: ClassVar[str] = "unknown"

    def close(self) -> None:  # noqa: B027 - 既定は何もしない
        """同期 HTTP セッション等を閉じる。"""

    async def aclose(self) -> None:  # noqa: B027
        """非同期 HTTP セッション等を閉じる。"""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()


class ChatCapability(Capability):
    # サーバー側で実行できる組み込みツール (ADR-09)。ラッパーはインスタンスで上書きする
    supported_builtins: frozenset[BuiltinTool] = frozenset()
    default_model: str | None = None

    @abstractmethod
    def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> ChatResponse: ...

    @abstractmethod
    async def agenerate(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> ChatResponse: ...

    @abstractmethod
    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> Iterator[StreamEvent]: ...

    @abstractmethod
    def astream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Sequence[ToolSpec] | None = None,
        tool_choice: ToolChoice = "auto",
        response_schema: type[BaseModel] | None = None,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> AsyncIterator[StreamEvent]: ...


class JudgeCapability(Capability):
    @abstractmethod
    def evaluate(
        self,
        state: str | dict[str, Any],
        questions: dict[str, JudgeQuestion],
        *,
        model: str | None = None,
        **provider_options: Any,
    ) -> JudgeResponse: ...

    @abstractmethod
    async def aevaluate(
        self,
        state: str | dict[str, Any],
        questions: dict[str, JudgeQuestion],
        *,
        model: str | None = None,
        **provider_options: Any,
    ) -> JudgeResponse: ...


class ImageCapability(Capability):
    @abstractmethod
    def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
        **provider_options: Any,
    ) -> ImageResponse: ...

    @abstractmethod
    async def agenerate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
        **provider_options: Any,
    ) -> ImageResponse: ...


AudioFormat = Literal["mp3", "wav", "opus", "pcm"]


class AudioCapability(Capability):
    @abstractmethod
    def speak(
        self,
        text: str,
        *,
        model: str | None = None,
        voice: str = "alloy",
        response_format: AudioFormat = "mp3",
        **provider_options: Any,
    ) -> AudioSpeechResponse: ...

    @abstractmethod
    async def aspeak(
        self,
        text: str,
        *,
        model: str | None = None,
        voice: str = "alloy",
        response_format: AudioFormat = "mp3",
        **provider_options: Any,
    ) -> AudioSpeechResponse: ...

    @abstractmethod
    def transcribe(
        self,
        audio: MediaSource,
        *,
        model: str | None = None,
        language: str | None = None,
        **provider_options: Any,
    ) -> AudioTranscriptionResponse: ...

    @abstractmethod
    async def atranscribe(
        self,
        audio: MediaSource,
        *,
        model: str | None = None,
        language: str | None = None,
        **provider_options: Any,
    ) -> AudioTranscriptionResponse: ...


EmbeddingTask = Literal["query", "document"]


class EmbeddingCapability(Capability):
    """埋め込み。共通の決まり（実装設計書 §14）:

    - ``texts`` が空リストなら API を呼ばずに空の応答を返す。空文字列は ``InvalidRequestError``
    - ``normalize=True``（既定）で L2 正規化して返す（プロバイダ間で cosine 類似度の扱いを揃える）
    - ``task`` は検索の非対称性（query / document）。対応しないプロバイダは無視する（debug ログ）
    - ``dimensions`` を縮められないモデルで指定すると ``UnsupportedFeatureError``
    """

    default_model: str | None = None

    @abstractmethod
    def embed(
        self,
        texts: str | Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
        task: EmbeddingTask | None = None,
        normalize: bool = True,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> EmbeddingResponse: ...

    @abstractmethod
    async def aembed(
        self,
        texts: str | Sequence[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
        task: EmbeddingTask | None = None,
        normalize: bool = True,
        timeout: float | None = None,
        **provider_options: Any,
    ) -> EmbeddingResponse: ...


class RerankCapability(Capability):
    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
        **provider_options: Any,
    ) -> RerankResponse: ...

    @abstractmethod
    async def arerank(
        self,
        query: str,
        documents: list[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
        **provider_options: Any,
    ) -> RerankResponse: ...


class SearchCapability(Capability):
    @abstractmethod
    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **provider_options: Any,
    ) -> SearchResponse: ...

    @abstractmethod
    async def asearch(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **provider_options: Any,
    ) -> SearchResponse: ...


class DocumentParserCapability(Capability):
    @abstractmethod
    def parse(
        self, file_source: MediaSource, *, mime_type: str = "application/pdf", **provider_options: Any
    ) -> ParsedDocument: ...

    @abstractmethod
    async def aparse(
        self, file_source: MediaSource, *, mime_type: str = "application/pdf", **provider_options: Any
    ) -> ParsedDocument: ...


class SandboxCapability(Capability):
    @abstractmethod
    def execute_python(
        self, code: str, *, timeout_seconds: int = 30, **provider_options: Any
    ) -> SandboxExecutionResult: ...

    @abstractmethod
    async def aexecute_python(
        self, code: str, *, timeout_seconds: int = 30, **provider_options: Any
    ) -> SandboxExecutionResult: ...


class MCPCapability(Capability):
    @abstractmethod
    async def alist_tools(self) -> list[ToolDefinition]: ...

    @abstractmethod
    async def acall_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...
