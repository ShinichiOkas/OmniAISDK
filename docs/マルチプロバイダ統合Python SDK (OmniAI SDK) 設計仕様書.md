# **マルチプロバイダ統合Python SDK (OmniAI SDK v3.0 \- Production Edition) 設計仕様書**

# **1\. 概要と設計思想**

## **1.1 背景と目的**

現代の生成AI・エージェントシステムおよび高精度RAG（検索拡張生成）は、単一のプロバイダだけでは完結せず、役割ごとに最適な専門API・ローカルモデルを組み合わせるハイブリッド構成が主流となっています。

* 高度な対話・推論・ツール連携: [OpenAI API](https://developers.openai.com/api/reference/overview) や [Anthropic Claude API](https://platform.claude.com/docs/en/api/overview)  
* 超長文コンテキスト解析・マルチモーダル処理: [Google Gemini API](https://aistudio.google.com/api-reference)  
* ローカルGPU実行による機密保護・ゼロコスト運用: [Ollama](https://docs.ollama.com/api/introduction)  
* 高速・安価な型付き決定論的評価（System One）: [TypeSafe Jev](https://docs.typesafe.ai/introduction)  
* 高精度RAGおよび外部連携: [Cohere Rerank](https://cohere.com/rerank)、[Tavily Search](https://tavily.com/)、[E2B Sandbox](https://e2b.dev/)、MCP (Model Context Protocol)

本SDK（OmniAI SDK v3.0）は、全10領域のモダリティを適材適所でミックス利用できるだけでなく、**プロダクション運用に必須となる非同期I/O（`async/await`）、マルチモーダル型、統一例外階層と自動フェイルオーバー、メモリ安全なストリームI/O、およびコスト・レイテンシ監視（Observability）**を完全統合したエンタープライズ仕様を定義します。

## **1.2 v3.0 における7大アーキテクチャ強化ポイント**

1. **Sync / Async デュアルインターフェース**: 全10領域のCapabilityに同期メソッドと非同期メソッド（`agenerate`, `astream`, `aevaluate` 等）および `async with` コンテキストマネージャを標準実装し、`asyncio.gather` による並列パイプライン実行を実現します。  
2. **マルチモーダル入力 (`ContentPart`) の完全型付け**: `ChatMessage.content` に単純な文字列だけでなく、テキスト・画像・音声・PDF文書を混在できる `List[ContentPart]` を導入します。  
3. **メモリ安全なメディア抽象化 (`MediaSource`)**: 巨大な音声・動画・PDFでOOM（メモリ枯渇）を起こさないよう、生 `bytes` だけでなく `pathlib.Path`、`BinaryIO` ストリーム、プロバイダ側のアップロード済み `FileRef` を透過的に扱います。  
4. **強固なツール定義 (`ToolDefinition` & `@omni_tool`)**: 生辞書を排除し、Python関数の型ヒントとDocstringからJSON Schemaを自動生成するデコレータと、MCPサーバーのツール定義を直結できる統一型を提供します。  
5. **構造化ストリーミング (`StreamEvent`)**: ストリーミング時にもテキスト差分だけでなく、Tool Callチャンク、完了理由（`finish_reason`）、最終トークン消費量（`Usage`）を欠落なく取得可能にします。  
6. **統一例外階層と `FallbackAdapter`**: プロバイダ固有のエラー（OpenAIの429やClaudeの529等）を `OmniError` 階層に正規化し、障害時に自動で別プロバイダ（例: OpenAI ➔ Anthropic ➔ ローカルOllama）へ切り替えるフェイルオーバー機構を提供します。  
7. **横断オブザーバビリティ (`ResponseMeta` & `TelemetryHook`)**: すべてのレスポンスに `latency_ms`・`estimated_cost_usd`・`request_id` を付与し、複数社APIを跨いだ1リクエストあたりの総コストと所要時間を自動集計します。

# **2\. プロバイダ機能マトリクス（全10領域）**

| 領域 / 役割 | インターフェース | 主要対応プロバイダ | コア仕様（共通積集合） | プロバイダ固有拡張 |
| :---- | :---- | :---- | :---- | :---- |
| **1\. テキスト・マルチモーダル対話** | `ChatCapability` | OpenAI, Anthropic, Gemini, Ollama | マルチモーダル `messages`, `tools`, `response_schema`, 同期/非同期 `StreamEvent` | Prompt Caching、Thinking Budget、推論effort指定 |
| **2\. 判定モデル (Judge)** | `JudgeCapability` | TypeSafe Jev, OpenAI Moderation, 汎用LLM-as-a-Judge | `state` に対する型付き質問（`Noul`, `Choice`, `Score`）と較正済み確率値の返却 | Jevの並列一括評価、画像領域判定 (Box) |
| **3\. 画像生成 (Image)** | `ImageCapability` | Ollama (MLX Flux等), OpenAI (DALL-E 3 / GPT-Image), Gemini (Imagen 3\) | `prompt`, `size`, `n`, 出力バイナリ / ストリーム保存の統一 | Negative Prompt、Seed固定、スタイル指定 |
| **4\. 音声処理 (Audio)** | `AudioCapability` | OpenAI, Gemini, ローカルWhisper / Kokoro | TTS (`speak` / `aspeak`), STT (`transcribe` / `atranscribe`), `MediaSource` 対応 | 話速調整、ワード単位タイムスタンプ、双方向WebSocket |
| **5\. 埋め込み (Embed)** | `EmbeddingCapability` | OpenAI, Gemini, Ollama, Voyage AI | `texts: List[str]` から `List[List[float]]` へのベクトル変換 | 出力次元数の圧縮指定 (`dimensions`) |

| 6\. リランカー (Rerank) | `RerankCapability` | Cohere Rerank, Jina, Voyage, ローカルBGE-Reranker | `query` と `documents` から関連度順スコア付きリストへの再整列 | 多言語モデル指定、チャンク最大トークン調整 |
| :---- | :---- | :---- | :---- | :---- |
| **7\. Web検索 (Search)** | `SearchCapability` | Tavily, Exa, Brave Search, Google Grounding | `query`, `max_results` からタイトル・URL・本文スニペットの配列返却 | ドメインフィルタ、生HTML取得、日付範囲指定 |
| **8\. 文書解析 (DocParser)** | `DocumentParserCapability` | Mistral OCR, LlamaParse, Unstructured, ローカルMarker | `MediaSource` (PDF/Office) から構造化Markdownおよび表データの抽出 | 画像・図表切り出し、ページ単位メタデータ付与 |
| **9\. コード実行 (Sandbox)** | `SandboxCapability` | E2B Sandbox, Modal, Gemini Code Execution, ローカルDocker | Pythonコード文字列の隔離実行と標準出力・エラー・生成ファイルの取得 | 永続セッション維持、パッケージ動的インストール |
| **10\. 外部接続 (MCP)** | `MCPCapability` | Anthropic MCP準拠サーバー全般 (stdio / SSE) | MCPサーバーからの `ToolDefinition` 自動取得とツール実行のブリッジ | リソース購読、プロンプトテンプレート取得 |

# **3\. 統一例外階層とエラーハンドリング仕様 (`omni_ai/exceptions.py`)**

呼び出し側がプロバイダ固有のSDK例外に依存しないよう、すべてのアダプターは以下の標準例外に変換して送出します。

```py
from typing import Optional

class OmniError(Exception):
    """OmniAI SDK すべての例外の基底クラス"""
    def __init__(
        self,
        message: str,
        provider: str,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        retryable: bool = False,
        raw_error: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retryable = retryable
        self.raw_error = raw_error

class AuthenticationError(OmniError):
    """APIキー不正・権限不足 (401/403) - リトライ不可"""
    pass

class RateLimitError(OmniError):
    """レート制限到達 (429) - リトライおよびフォールバック対象"""
    def __init__(self, *args, retry_after_seconds: Optional[float] = None, **kwargs):
        super().__init__(*args, retryable=True, **kwargs)
        self.retry_after_seconds = retry_after_seconds

class ProviderOverloadedError(OmniError):
    """プロバイダ過負荷・一時障害 (500/502/503/529) - フォールバック対象"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, retryable=True, **kwargs)

class ContextLengthExceededError(OmniError):
    """最大コンテキスト長超過 (400) - リトライ不可"""
    pass

class SchemaValidationError(OmniError):
    """Structured Outputs または Judge の型検証失敗"""
    pass
```

# **4\. 共通データモデル・テレメトリ・マルチモーダル型 (`omni_ai/types.py`)**

```py
import inspect
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Literal, Optional, Type, Union
from pydantic import BaseModel, Field, create_model

# -------------------------------------------------------------
# 0. 共通テレメトリ & メディアソース抽象化
# -------------------------------------------------------------
class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    total_tokens: int = 0

class ResponseMeta(BaseModel):
    """全モダリティ共通の追跡・コスト計算メタデータ"""
    provider: str
    model: str
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    request_id: Optional[str] = None
    usage: Usage = Field(default_factory=Usage)

class FileRef(BaseModel):
    """プロバイダ側に事前アップロードされたファイル参照 (Gemini File API / OpenAI Files等)"""
    file_id: str
    uri: Optional[str] = None
    mime_type: str

# メモリ圧迫を防ぐ汎用メディア入力型 (bytes, ファイルパス, ストリーム, アップロード済み参照)
MediaSource = Union[bytes, Path, BinaryIO, FileRef, str]

# -------------------------------------------------------------
# 1. マルチモーダル Chat & Tool Calling データモデル
# -------------------------------------------------------------
class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str

class MediaPart(BaseModel):
    type: Literal["image", "audio", "video", "document"]
    source: MediaSource
    mime_type: str

ContentPart = Union[TextPart, MediaPart]

class ToolDefinition(BaseModel):
    """プロバイダ非依存の厳密なツール定義（MCPツールとも相互変換可能）"""
    name: str
    description: str
    parameters_schema: Dict[str, Any]  # JSON Schema
    strict: bool = True

def omni_tool(func: Callable) -> ToolDefinition:
    """Python関数からToolDefinitionを自動生成するデコレータ"""
    sig = inspect.signature(func)
    fields = {
        k: (v.annotation if v.annotation != inspect.Parameter.empty else str,
            ... if v.default == inspect.Parameter.empty else v.default)
        for k, v in sig.parameters.items()
    }
    pydantic_model = create_model(f"{func.__name__}_schema", **fields)
    return ToolDefinition(
        name=func.__name__,
        description=(func.__doc__ or "").strip(),
        parameters_schema=pydantic_model.model_json_schema(),
    )

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any]

class ChatMessage(BaseModel):
    role: Role
    content: Union[str, List[ContentPart]]
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None

class ChatResponse(BaseModel):
    content: Optional[str] = None
    parsed: Optional[Any] = None
    tool_calls: List[ToolCall] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter", "unknown"] = "stop"
    meta: ResponseMeta

class StreamEvent(BaseModel):
    """ストリーミング中の構造化イベント（テキスト・ツール呼び出し・完了時統計を統合）"""
    type: Literal["text_delta", "tool_call_delta", "message_complete"]
    delta_text: str = ""
    tool_call: Optional[ToolCall] = None
    finish_reason: Optional[str] = None
    meta: Optional[ResponseMeta] = None

# -------------------------------------------------------------
# 2. Judge / Decision モデル (TypeSafe Jev 互換プリミティブ)
# -------------------------------------------------------------
class NoulQuestion(BaseModel):
    kind: Literal["noul"] = "noul"
    instructions: str

class ChoiceQuestion(BaseModel):
    kind: Literal["choice"] = "choice"
    instructions: str
    criteria: Dict[str, Optional[str]]

class ScoreQuestion(BaseModel):
    kind: Literal["score"] = "score"
    instructions: str
    criteria: List[str]

JudgeQuestion = Union[NoulQuestion, ChoiceQuestion, ScoreQuestion]

class VerdictResult(BaseModel):
    value: Union[bool, str, float]
    probability: float
    distribution: Optional[Dict[str, float]] = None
    confidence: Optional[float] = None

class JudgeResponse(BaseModel):
    verdicts: Dict[str, VerdictResult]
    meta: ResponseMeta

# -------------------------------------------------------------
# 3. Image / Audio / Embedding / RAG / Sandbox モデル
# -------------------------------------------------------------
class GeneratedImage(BaseModel):
    data: bytes
    mime_type: str = "image/png"
    revised_prompt: Optional[str] = None

    def save(self, path: Union[str, Path]) -> Path:
        target = Path(path)
        target.write_bytes(self.data)
        return target

class ImageResponse(BaseModel):
    images: List[GeneratedImage]
    meta: ResponseMeta

class AudioSpeechResponse(BaseModel):
    audio_bytes: bytes
    format: Literal["mp3", "wav", "opus", "pcm"]
    meta: ResponseMeta

class AudioTranscriptionResponse(BaseModel):
    text: str
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    meta: ResponseMeta

class EmbeddingResponse(BaseModel):
    vectors: List[List[float]]
    dimensions: int
    meta: ResponseMeta

class RerankItem(BaseModel):
    index: int
    document: str
    relevance_score: float

class RerankResponse(BaseModel):
    results: List[RerankItem]
    meta: ResponseMeta

class SearchItem(BaseModel):
    title: str
    url: str
    content: str
    score: Optional[float] = None

class SearchResponse(BaseModel):
    results: List[SearchItem]
    meta: ResponseMeta

class ParsedDocument(BaseModel):
    markdown: str
    page_count: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    meta: ResponseMeta

class SandboxExecutionResult(BaseModel):
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifacts: List[bytes] = Field(default_factory=list)
    meta: ResponseMeta
```

# **5\. Sync / Async 対応 抽象インターフェース仕様 (`omni_ai/capabilities.py`)**

すべてのCapabilityにおいて、同期版と非同期版（`a` プレフィックス）の双方を提供します。

```py
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, Iterator, List, Literal, Optional, Type, Union
from pydantic import BaseModel
from .types import (
    ChatMessage, ChatResponse, StreamEvent, ToolDefinition,
    JudgeQuestion, JudgeResponse, ImageResponse,
    AudioSpeechResponse, AudioTranscriptionResponse, EmbeddingResponse,
    RerankResponse, SearchResponse, ParsedDocument, SandboxExecutionResult, MediaSource
)

class ChatCapability(ABC):
    @abstractmethod
    def generate(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDefinition]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        pass

    @abstractmethod
    async def agenerate(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[ToolDefinition]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        pass

    @abstractmethod
    def stream(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        tools: Optional[List[ToolDefinition]] = None,
        **kwargs: Any,
    ) -> Iterator[StreamEvent]:
        pass

    @abstractmethod
    def astream(
        self,
        messages: List[ChatMessage],
        model: Optional[str] = None,
        tools: Optional[List[ToolDefinition]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        pass


class JudgeCapability(ABC):
    @abstractmethod
    def evaluate(
        self,
        state: Union[str, Dict[str, Any]],
        questions: Dict[str, JudgeQuestion],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> JudgeResponse:
        pass

    @abstractmethod
    async def aevaluate(
        self,
        state: Union[str, Dict[str, Any]],
        questions: Dict[str, JudgeQuestion],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> JudgeResponse:
        pass


class ImageCapability(ABC):
    @abstractmethod
    def generate_image(
        self,
        prompt: str,
        model: Optional[str] = None,
        size: str = "1024x1024",
        n: int = 1,
        **kwargs: Any,
    ) -> ImageResponse:
        pass

    @abstractmethod
    async def agenerate_image(
        self,
        prompt: str,
        model: Optional[str] = None,
        size: str = "1024x1024",
        n: int = 1,
        **kwargs: Any,
    ) -> ImageResponse:
        pass


class AudioCapability(ABC):
    @abstractmethod
    def speak(
        self,
        text: str,
        model: Optional[str] = None,
        voice: str = "alloy",
        response_format: Literal["mp3", "wav", "opus", "pcm"] = "mp3",
        **kwargs: Any,
    ) -> AudioSpeechResponse:
        pass

    @abstractmethod
    async def aspeak(
        self,
        text: str,
        model: Optional[str] = None,
        voice: str = "alloy",
        response_format: Literal["mp3", "wav", "opus", "pcm"] = "mp3",
        **kwargs: Any,
    ) -> AudioSpeechResponse:
        pass

    @abstractmethod
    def transcribe(
        self,
        audio: MediaSource,
        model: Optional[str] = None,
        language: Optional[str] = None,
        **kwargs: Any,
    ) -> AudioTranscriptionResponse:
        pass

    @abstractmethod
    async def atranscribe(
        self,
        audio: MediaSource,
        model: Optional[str] = None,
        language: Optional[str] = None,
        **kwargs: Any,
    ) -> AudioTranscriptionResponse:
        pass


class EmbeddingCapability(ABC):
    @abstractmethod
    def embed(
        self,
        texts: Union[str, List[str]],
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        pass

    @abstractmethod
    async def aembed(
        self,
        texts: Union[str, List[str]],
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        pass


class RerankCapability(ABC):
    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: List[str],
        model: Optional[str] = None,
        top_n: Optional[int] = None,
        **kwargs: Any,
    ) -> RerankResponse:
        pass

    @abstractmethod
    async def arerank(
        self,
        query: str,
        documents: List[str],
        model: Optional[str] = None,
        top_n: Optional[int] = None,
        **kwargs: Any,
    ) -> RerankResponse:
        pass


class SearchCapability(ABC):
    @abstractmethod
    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **kwargs: Any,
    ) -> SearchResponse:
        pass

    @abstractmethod
    async def asearch(
        self,
        query: str,
        max_results: int = 5,
        search_depth: Literal["basic", "advanced"] = "basic",
        **kwargs: Any,
    ) -> SearchResponse:
        pass


class DocumentParserCapability(ABC):
    @abstractmethod
    def parse(
        self,
        file_source: MediaSource,
        mime_type: str = "application/pdf",
        **kwargs: Any,
    ) -> ParsedDocument:
        pass

    @abstractmethod
    async def aparse(
        self,
        file_source: MediaSource,
        mime_type: str = "application/pdf",
        **kwargs: Any,
    ) -> ParsedDocument:
        pass


class SandboxCapability(ABC):
    @abstractmethod
    def execute_python(
        self,
        code: str,
        timeout_seconds: int = 30,
        **kwargs: Any,
    ) -> SandboxExecutionResult:
        pass

    @abstractmethod
    async def aexecute_python(
        self,
        code: str,
        timeout_seconds: int = 30,
        **kwargs: Any,
    ) -> SandboxExecutionResult:
        pass


class MCPCapability(ABC):
    @abstractmethod
    async def alist_tools(self) -> List[ToolDefinition]:
        """MCPサーバーのツール一覧をSDK標準のToolDefinitionリストとして取得"""
        pass

    @abstractmethod
    async def acall_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        pass
```

# **6\. 自動フェイルオーバー＆テレメトリ対応ルーター (`omni_ai/router.py`)**

プロバイダ障害時の自動切り替え（`FallbackChatAdapter`）およびセッション全体のコスト・レイテンシ集計機構を備えた `OmniClient` の仕様です。

```py
from typing import Callable, List, Optional
from .exceptions import OmniError
from .types import ResponseMeta
from .capabilities import (
    ChatCapability, JudgeCapability, ImageCapability, AudioCapability,
    EmbeddingCapability, RerankCapability, SearchCapability,
    DocumentParserCapability, SandboxCapability, MCPCapability
)

class FallbackChatAdapter(ChatCapability):
    """プライマリプロバイダがRateLimitや過負荷(529/503)で失敗した際、自動でセカンダリへ切り替えるラッパー"""
    def __init__(self, providers: List[ChatCapability]):
        if not providers:
            raise ValueError("At least one provider is required.")
        self.providers = providers

    async def agenerate(self, *args, **kwargs):
        last_err = None
        for provider in self.providers:
            try:
                return await provider.agenerate(*args, **kwargs)
            except OmniError as e:
                last_err = e
                if not e.retryable:
                    raise
                continue
        raise last_err

    # generate, stream, astream も同様に順次フェイルオーバー実装


class TelemetryTracker:
    """複数プロバイダを跨いだAPI呼び出しの累計コストとレイテンシを集計"""
    def __init__(self):
        self.history: List[ResponseMeta] = []

    def record(self, meta: ResponseMeta) -> None:
        self.history.append(meta)

    @property
    def total_cost_usd(self) -> float:
        return sum(m.estimated_cost_usd for m in self.history)

    @property
    def total_latency_ms(self) -> float:
        return sum(m.latency_ms for m in self.history)


class OmniClient:
    """全10領域のアダプターとテレメトリ・非同期ライフサイクルを統合管理するクライアント"""

    def __init__(
        self,
        chat: Optional[ChatCapability] = None,
        judge: Optional[JudgeCapability] = None,
        image: Optional[ImageCapability] = None,
        audio: Optional[AudioCapability] = None,
        embedding: Optional[EmbeddingCapability] = None,
        rerank: Optional[RerankCapability] = None,
        search: Optional[SearchCapability] = None,
        doc_parser: Optional[DocumentParserCapability] = None,
        sandbox: Optional[SandboxCapability] = None,
        mcp: Optional[MCPCapability] = None,
        on_response: Optional[Callable[[ResponseMeta], None]] = None,
    ):
        self._chat = chat
        self._judge = judge
        self._image = image
        self._audio = audio
        self._embedding = embedding
        self._rerank = rerank
        self._search = search
        self._doc_parser = doc_parser
        self._sandbox = sandbox
        self._mcp = mcp
        self.telemetry = TelemetryTracker()
        self._on_response = on_response

    async def __aenter__(self) -> "OmniClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        # 各アダプターが保持する非同期HTTPセッション (httpx.AsyncClient等) を安全にクローズ
        pass

    @property
    def chat(self) -> ChatCapability:
        if not self._chat:
            raise NotImplementedError("Chat provider is not configured.")
        return self._chat

    @property
    def judge(self) -> JudgeCapability:
        if not self._judge:
            raise NotImplementedError("Judge provider is not configured.")
        return self._judge

    @property
    def image(self) -> ImageCapability:
        if not self._image:
            raise NotImplementedError("Image provider is not configured.")
        return self._image

    @property
    def audio(self) -> AudioCapability:
        if not self._audio:
            raise NotImplementedError("Audio provider is not configured.")
        return self._audio

    @property
    def embedding(self) -> EmbeddingCapability:
        if not self._embedding:
            raise NotImplementedError("Embedding provider is not configured.")
        return self._embedding

    @property
    def rerank(self) -> RerankCapability:
        if not self._rerank:
            raise NotImplementedError("Rerank provider is not configured.")
        return self._rerank

    @property
    def search(self) -> SearchCapability:
        if not self._search:
            raise NotImplementedError("Search provider is not configured.")
        return self._search

    @property
    def doc_parser(self) -> DocumentParserCapability:
        if not self._doc_parser:
            raise NotImplementedError("DocumentParser provider is not configured.")
        return self._doc_parser

    @property
    def sandbox(self) -> SandboxCapability:
        if not self._sandbox:
            raise NotImplementedError("Sandbox provider is not configured.")
        return self._sandbox

    @property
    def mcp(self) -> MCPCapability:
        if not self._mcp:
            raise NotImplementedError("MCP provider is not configured.")
        return self._mcp
```

# **7\. v3.0 統合実装サンプル（非同期並列実行・フォールバック・マルチモーダル・コスト集計）**

以下のコードは、v3.0で強化された **非同期並列処理 (`asyncio.gather`)、OpenAI ➔ Claude 自動フェイルオーバー、Pathオブジェクトによるメモリ安全なPDF解析、Jevによる高速判定、ローカルOllamaでの画像生成、および総コスト追跡** を組み合わせたプロダクション実装例です。

```py
import asyncio
from pathlib import Path
from omni_ai import OmniClient, FallbackChatAdapter
from omni_ai.providers import (
    OpenAIChatAdapter,       # プライマリ対話: OpenAI
    AnthropicChatAdapter,    # バックアップ対話: Anthropic Claude
    JevJudgeAdapter,         # 高速判定: TypeSafe Jev
    OllamaImageAdapter,      # 画像生成: Ollama (ローカル MLX Flux)
    CohereRerankAdapter,     # リランク: Cohere Rerank
    TavilySearchAdapter,     # Web検索: Tavily Search
    MistralOCRAdapter,       # 文書解析: Mistral OCR
)
from omni_ai.types import (
    ChatMessage, Role, TextPart, MediaPart,
    NoulQuestion, ChoiceQuestion, omni_tool
)

@omni_tool
def calculate_roi(initial_cost: float, annual_savings: float) -> float:
    """投資回収年数を計算するツール"""
    return round(initial_cost / annual_savings, 2)

async def main():
    # 1. OpenAIがレート制限や障害の時は自動でAnthropicへフォールバックする構成
    resilient_chat = FallbackChatAdapter([
        OpenAIChatAdapter(default_model="gpt-4o"),
        AnthropicChatAdapter(default_model="claude-3-5-sonnet-latest"),
    ])

    async with OmniClient(
        chat=resilient_chat,
        judge=JevJudgeAdapter(default_model="jev-latest"),
        image=OllamaImageAdapter(base_url="http://localhost:11434", default_model="x/flux2-klein:4b"),
        rerank=CohereRerankAdapter(default_model="rerank-v3.5"),
        search=TavilySearchAdapter(),
        doc_parser=MistralOCRAdapter(),
    ) as ai:

        query = "添付の技術レポートPDFと最新のWeb動向から、ペロブスカイト太陽電池の導入メリットを要約して"

        # 2. 【非同期並列実行】ローカルPDFのOCR解析（Path渡しで省メモリ）とWeb検索を同時実行
        parsed_doc, search_res = await asyncio.gather(
            ai.doc_parser.aparse(Path("./internal_solar_report.pdf")),
            ai.search.asearch(query="ペロブスカイト太陽電池 実用化 2026", max_results=8),
        )

        # 3. 【リランク: Cohere】Web検索結果とPDF抜粋を統合して上位3件に絞り込み
        candidate_chunks = [parsed_doc.markdown[:2000]] + [r.content for r in search_res.results]
        reranked = await ai.rerank.arerank(query=query, documents=candidate_chunks, top_n=3)
        best_context = "\n---\n".join([item.document for item in reranked.results])

        # 4. 【判定: Jev】根拠の十分性と安全性をミリ秒単位で非同期評価
        judgment = await ai.judge.aevaluate(
            state={"query": query, "context": best_context},
            questions={
                "is_grounded": NoulQuestion(instructions="コンテキスト内に回答に必要な具体的根拠が含まれているか？"),
                "audience": ChoiceQuestion(
                    instructions="最適な説明トーンの選択",
                    criteria={"executive": "経営層向け", "engineer": "技術者向け"}
                ),
            }
        )

        if not judgment.verdicts["is_grounded"].value:
            raise RuntimeError("根拠データが不足しているため回答生成を中止しました。")

        # 5. 【マルチモーダル対話 + ツール連携】参照チャート画像とテキストを同時に渡して推論
        chat_res = await ai.chat.agenerate(
            messages=[
                ChatMessage(
                    role=Role.USER,
                    content=[
                        TextPart(text=f"以下の根拠に基づき要約してください:\n{best_context}"),
                        MediaPart(type="image", source=Path("./reference_chart.png"), mime_type="image/png"),
                    ],
                )
            ],
            tools=[calculate_roi],
        )
        print(f"--- 回答 ({chat_res.meta.provider} / {chat_res.meta.latency_ms:.0f}ms) ---\n{chat_res.content}\n")

        # 6. 【画像生成: Ollama】ローカルGPUでプレゼン用挿絵を非同期生成
        img_res = await ai.image.agenerate_image(
            prompt="Modern building facade with transparent perovskite solar glass, architectural visualization",
            size="1024x1024",
        )
        img_res.images[0].save("output_slide_graphic.png")

        # 7. 【オブザーバビリティ】全プロバイダ横断のコスト・レイテンシ確認
        print(f"Total Workflow Cost: ${ai.telemetry.total_cost_usd:.5f} USD")

if __name__ == "__main__":
    asyncio.run(main())
```

# **8\. ツールコーリングの実行階層とハイブリッド実行ルーター仕様**

## **8.1 ツールコーリングの3つの実行階層（Execution Layers）**

ツールコーリング（Function Calling）は、「誰がそのツールを実際に実行（Execute）するか」によって以下の3階層に分類されます。  
**Layer 1: クライアントサイド実行型 (Local Function Calling)**  
実行主体: アプリケーション（クライアント）側  
フロー: モデルは関数の引数JSON（tool\_calls）を出力するのみ。クライアントが自社DBやローカルAPIを実行し、結果（tool\_result）をLLMへ再送信するマルチターン・ループが必要。  
用途: 社内DBアクセス、社内システムAPI、ローカルファイル操作などの機密・プライベート処理。  
**Layer 2: サーバーサイド組み込み実行型 (Provider-Hosted / Built-in Tools)**  
実行主体: プロバイダ側のクラウド基盤（Web検索エンジン、隔離Pythonコンテナ等）  
フロー: クライアントはツールシンボル（web\_search や code\_execution 等）を指定するのみ。モデルがツール呼び出しを決定すると、プロバイダのサーバーが自動でツールを実行し、結果をコンテキストに再投入して最終回答まで1リクエストで完結させる。  
用途: リアルタイムWeb検索、計算・データ集計用Pythonサンドボックス実行、プロバイダ側RAG（File Search）。  
**Layer 3: プロトコル接続型 (MCP: Model Context Protocol)**  
実行主体: リモートまたはローカルのMCPサーバー  
フロー: Anthropicが策定したオープン規格に準拠し、リモートMCPサーバーのエンドポイントからツール定義を取得して直接実行する。

## **8.2 各社プロバイダのツール実行モデル 徹底比較表**

| 比較項目 | OpenAI API | Anthropic Claude API | Google Gemini API | Ollama API |
| :---- | :---- | :---- | :---- | :---- |
| **自作ツールの実行方式(Layer 1\)** | クライアント実行type: "function" を定義し、返ってきた引数をアプリが実行して結果を再送信。 | クライアント実行tools に定義し、tool\_use ブロックを受信後、tool\_result を再送信。 | クライアント実行function\_declarations を定義し、function\_call を受けて function\_response を再送信。 | クライアント実行tools を指定し、返ってきた tool\_calls をアプリがローカルで実行して再送信。 |
| **サーバー側Web検索(Layer 2\)** | 提供ありResponses APIの組み込み web\_search。OpenAI側が検索して引用元付きで回答。 | 提供ありMessages APIの組み込み web\_search (Server Tools)。Anthropic側が自律実行。 | 提供ありGoogle Search Grounding (google\_search)。Google検索エンジンが直接実行。 | なし(完全ローカル環境のため、クライアントが外部検索APIを叩く必要あり) |
| **サーバー側コード実行(Layer 2\)** | 提供ありcode\_interpreter。クラウドコンテナでPythonを実行し、グラフや計算結果を返却。 | 提供ありServer Toolsの code\_execution。Anthropicのサンドボックス環境でPythonを実行。 | 提供ありCode Execution (code\_execution)。GoogleのPythonサンドボックスで自律計算。 | なし(ローカル端末での実行となり、安全対策はアプリ側の責任) |
| **その他のサーバー機能** | ・file\_search (OpenAI側RAG)・image\_generation (DALL-E) | ・web\_fetch (URL先コンテンツ取得)・(特殊) computer\_use (指示はモデル、実行はクライアント) | ・Google Maps連携 (google\_maps)・指定URLの事前解析 (url\_context) | なし |
| **リモートMCP連携(Layer 3\)** | 対応Responses APIからリモートMCPサーバーへ直接接続可能。 | ネイティブ対応Messages APIからリモートMCPツールを直接ロード・実行可能。 | SDK/エコシステム対応クライアントSDKやエージェントフレームワークで連携。 | クライアント側対応LangChain等の上位レイヤー側でMCPツールをバインド。 |
| **エージェントループ** | Responses APIが自動管理(1リクエスト内で複数ツールをサーバー側で自律反復) | Server Tools時はサーバー側で自動完結(クライアントツールの場合はアプリ側でループ) | サーバー側で自律実行(SearchやCode Executionは1ターンで内部完結) | 100% クライアント側でループ |

## **8.3 各社の特徴とアーキテクチャ上の注意点**

**OpenAI:**  
従来の /v1/chat/completions ではクライアントサイド実行（Layer 1）のみをサポート。  
最新の /v1/responses（およびAssistants API）では、web\_search や code\_interpreter などの組み込みツールがプロバイダ側（Layer 2）で実行可能。自作関数（type: "function"）と組み込みツールを同一配列で渡せ、組み込みツール実行時はOpenAI側でループを自動処理。  
**Google Gemini:**  
Googleの検索インフラと直結した Google Search Grounding（google\_search）により、最新情報の引用メタデータ付き取得が極めて低遅延。  
code\_execution を有効化すると、数学問題やデータ集計に対してGemini自身が裏でPythonコードを生成・Googleクラウドサンドボックスで実行・エラー自己修正を行い、正しい計算結果のみを返却。  
**Anthropic (Claude):**  
最新仕様で web\_search や code\_execution を Server Tools（Anthropicのサーバー側で自動ループ実行されるツール）として正式導入。  
Computer Use (computer\_20241022) は、ツールスキーマ定義はAnthropicが事前提供するが、実際のOS画面操作やVM実行はクライアント側（Docker等）で走らせるハイブリッド設計。  
**Ollama:**  
完全ローカル・オフライン動作のため、クラウド側のマネージドツール（Google検索やホスト型コンテナ）は一切持たない。  
モデルが引数JSONを出力した後は、100%クライアント側（SDKまたはアプリ）が外部検索APIやローカルDockerを実行して結果を差し戻すエージェントループを回す必要がある。

## **8.4 ハイブリッド・ツール実行ルーター設計 (omni\_ai/tools.py)**

OmniAI SDKでは、「サーバー側実行ができるプロバイダ（OpenAI/Gemini/Claude）」と「自力で実行しなければならないプロバイダ（Ollama）」の差異を吸収するため、SDK共通の組み込みツールシンボル（BuiltinTool） と 透過的エージェントループ（HybridToolRouter） を提供します。

```py
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from .types import ChatMessage, ChatResponse, Role, ToolDefinition

class BuiltinTool(str, Enum):
    WEB_SEARCH = "builtin::web_search"
    CODE_EXECUTION = "builtin::code_execution"

class HybridToolManager:
    """
    プロバイダのサーバー実行可否を判定し、
    ネイティブサーバーツール変換 or クライアント側自動実行エージェントループを透過的に行う機構
    """
    def __init__(self, search_capability=None, sandbox_capability=None):
        self.search = search_capability
        self.sandbox = sandbox_capability

    def adapt_tools_for_provider(
        self,
        tools: List[Any],
        provider_name: str,
    ) -> tuple[List[Any], Dict[str, Callable]]:
        """
        指定されたプロバイダ向けにツールリストを最適化する。
        - プロバイダがサーバー実行対応 (OpenAI/Gemini/Claude) の場合: ネイティブ組み込みツール型にマッピング
        - プロバイダが非対応 (Ollama等) の場合: クライアント実行用ToolDefinitionに変換し、ローカル実行ハンドラを登録
        """
        api_tools = []
        client_handlers: Dict[str, Callable] = {}

        for t in tools:
            if t == BuiltinTool.WEB_SEARCH:
                if provider_name == "openai":
                    api_tools.append({"type": "web_search"})
                elif provider_name == "gemini":
                    api_tools.append({"type": "google_search"})
                elif provider_name == "anthropic":
                    api_tools.append({"type": "web_search_20260209"})
                elif provider_name == "ollama":
                    # Ollama向け: SDKのSearchCapabilityをローカルFunction Callingとして定義
                    def _local_search(query: str) -> str:
                        res = self.search.search(query=query, max_results=5)
                        return "\n".join([f"[{r.title}] {r.content}" for r in res.results])
                    
                    api_tools.append(ToolDefinition(
                        name="web_search",
                        description="インターネット上の最新情報を検索する",
                        parameters_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string", "description": "検索クエリ"}},
                            "required": ["query"],
                        }
                    ))
                    client_handlers["web_search"] = _local_search
            elif isinstance(t, ToolDefinition):
                api_tools.append(t)

        return api_tools, client_handlers

    async def execute_agent_loop(
        self,
        chat_capability,
        messages: List[ChatMessage],
        tools: List[Any],
        max_turns: int = 5,
        **kwargs: Any,
    ) -> ChatResponse:
        """
        Ollama等のクライアント実行型プロバイダに対し、
        ツール呼び出しが不要になるまで自動でローカル関数を実行して結果を差し戻すループ
        """
        provider_name = chat_capability.provider_name
        api_tools, handlers = self.adapt_tools_for_provider(tools, provider_name)

        current_messages = list(messages)
        for _ in range(max_turns):
            response = await chat_capability.agenerate(
                messages=current_messages,
                tools=api_tools,
                **kwargs,
            )
            # ツール呼び出しがなければ完了
            if not response.tool_calls:
                return response

            # サーバー側実行ツール（OpenAI Responses API等）ならすでに完了している
            if response.finish_reason == "stop" and not handlers:
                return response

            # クライアント実行ハンドラが存在する場合は自動実行してループ継続
            for tc in response.tool_calls:
                if tc.name in handlers:
                    tool_result = handlers[tc.name](**tc.arguments)
                    current_messages.append(ChatMessage(
                        role=Role.TOOL,
                        content=str(tool_result),
                        tool_call_id=tc.id,
                    ))
        return response
```

## **8.5 利用者側のメリット**

開発者はプロバイダごとの仕様差を意識することなく、以下のように一行でツールを指定できます。

```py
# OpenAIやGeminiではサーバー側インフラで超低遅延実行され、
# OllamaではSDKが自動で裏でTavily検索を実行して結果を差し戻す。
response = await ai.chat.agenerate(
    messages=[ChatMessage(role=Role.USER, content="2026年最新のAI動向を調べて")],
    tools=[BuiltinTool.WEB_SEARCH],
)
```

# **9\. マルチプラットフォーム（Windows / Linux / macOS）実装規約とアーキテクチャ決定記録 (ADR)**

Windows、Linux、macOS の3大OS環境（およびDocker/WSL2環境）で一切の挙動差異やビルドエラーを起こさせず、開発者が実装時に迷わないための**アーキテクチャ決定記録（ADR: Architecture Decision Records）**と**OS差分吸収レイヤー仕様**を定義します。

## **9.1 実装における5大アーキテクチャ決定事項 (ADR)**

| ADR番号 | 決定テーマ | 採用方針（結論） | 理由・背景 |
| :---- | :---- | :---- | :---- |
| **ADR-01** | **OS差分の隔離場所** | **omni\_ai/compat/ 単一モジュールに完全隔離**（各アダプター内での `sys.platform` や `os.name` 分岐を全面禁止） | アダプターやビジネスロジック内にOS判定 (`if sys.platform == "win32":`) が散在するとテスト漏れや保守破綻を招くため、OS固有処理はすべて `compat` レイヤーの関数経由で呼び出す。 |
| **ADR-02** | **HTTP通信基盤の統一** | **httpx \+ anyio ベースのPure Python実装を標準採用**（各社公式SDKへのハード依存を排除） | 全10社の公式SDKを必須依存にするとPydanticやgRPC/C拡張のバージョン競合が起き、特にWindows環境でC++ビルドツール不足による `pip install` 失敗が多発するため。 |
| **ADR-03** | **パッケージ配布形態** | **Pure Python Wheel (py3-none-any.whl) の保証**コア依存は `pydantic`, `httpx`, `platformdirs`, `anyio` の4つのみ | OS固有のネイティブバイナリ（Rust/C++コンパイル）をコアから排除し、Windows / Linux (x86\_64, ARM64) / macOS (Intel, Apple Silicon) すべてで単一Wheelが即時インストールできるようにする。 |
| **ADR-04** | **設定と認証の解決順序** | **①引数明示 ➔ ②OMNI\_\*環境変数 ➔ ③各社標準環境変数 ➔ ④OS標準設定ファイル** の厳格な4段階優先順位 | 開発環境（`.env` / 設定ファイル）と本番コンテナ環境（環境変数注入）でコード変更なしに挙動を統一するため。 |
| **ADR-05** | **テストマトリクス基準** | **GitHub Actions 等での3 OS × Python 3.11+ マトリクスCI必須化** | パス区切り文字やファイルロック、文字コードのバグをPR段階で機械的に検出するため。 |

## **9.2 OS差分が発生する5つの領域と標準吸収ルール**

| 領域 | Windows 特有の挙動・課題 | Linux 特有の挙動・課題 | macOS 特有の挙動・課題 | SDKでの標準吸収ルール（omni\_ai/compat） |
| :---- | :---- | :---- | :---- | :---- |
| **1\. ファイルパスと文字コード** | ・バックスラッシュ (`\`) 区切り・標準エンコーディングが `cp932` (Shift-JIS)・開いている一時ファイルを別プロセスから開けない排他ロック仕様 | ・スラッシュ (`/`) 区切り・大文字小文字を厳密に区別 (Case-sensitive)・UTF-8標準 | ・スラッシュ (`/`) 区切り・デフォルトAPFSは大文字小文字を区別しない・ファイル名濁点のNFD正規化問題 | ・文字列連結や `os.path` を禁止し **`pathlib.Path` に完全統一**・全ファイルI/Oで `encoding="utf-8"` を強制・ファイル名比較時は `unicodedata.normalize("NFC", name)` を適用・一時ファイルは閉じてからパスを渡す共通コンテキストマネージャを使用 |
| **2\. ローカルAI・GPUバックエンド** | ・NVIDIA CUDA または DirectML・OllamaのMLX系画像生成モデル (`x/flux2-klein`等) が非対応の場合あり | ・NVIDIA CUDA / AMD ROCm・サーバー用途ではVRAM管理が厳格 | ・Apple Silicon Unified Memory (Metal / MLX)・OllamaのMLX画像生成がネイティブ高速稼働 | ・`compat.detect_local_capabilities()` でローカルOllamaの対応機能を事前プローブ・ローカル画像生成非対応OSでは自動的にクラウド `ImageCapability` (OpenAI/Gemini) またはローカルDiffusers互換APIへ透過フォールバック |
| **3\. サブプロセス・MCP (stdio) 起動** | ・`npx` や `uvx` が `.cmd` / `.exe` 拡張子なしでは `FileNotFoundError` になる・`SIGTERM` が機能せずプロセスツリーが孤児化しやすい | ・POSIXシグナル (`SIGTERM` \-\> `SIGKILL`) で正常終了可能 | ・Linux同様POSIX準拠だが、GUI起動時は環境変数 `PATH` が最小限になりやすい | ・コマンド実行前に必ず **`shutil.which()` でフルパス解決**・プロセス終了時はWindowsでは `taskkill /F /T /PID` または Job Object、POSIXではプロセスグループ (`os.killpg`) で子プロセスごと確実に終了 |
| **4\. キャッシュ・設定保存先ディレクトリ** | `%LOCALAPPDATA%\omni_ai` | `~/.config/omni_ai``~/.cache/omni_ai` (XDG準拠) | `~/Library/Application Support/omni_ai``~/Library/Caches/omni_ai` | ・`~/.omni_ai` のような直書きを禁止し、**`platformdirs` ライブラリでOS標準パスを自動取得** |
| **5\. ローカルホスト・コンテナ通信** | ・WSL2内やDocker Desktopからは `host.docker.internal` またはvEthernet IPが必要 | ・Docker内からは `172.17.0.1` または `--network host` が必要 | ・Docker Desktop内からは `host.docker.internal` が必要 | ・`localhost:11434` 接続失敗時、コンテナ内実行を検知したら自動で `host.docker.internal` 等へフォールバック解決 |

## **9.3 OS差分吸収レイヤーの実装仕様 (omni\_ai/compat.py)**

すべてのアダプターが必ず経由する、OS差分吸収ユーティリティの標準実装仕様です。

```py
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, List, Optional
from platformdirs import user_cache_dir, user_config_dir

APP_NAME = "omni_ai"

class PlatformCompat:
    """Windows / Linux / macOS のOS差分を一元吸収するファサード"""

    @staticmethod
    def is_windows() -> bool:
        return sys.platform == "win32"

    @staticmethod
    def is_macos() -> bool:
        return sys.platform == "darwin"

    @staticmethod
    def is_linux() -> bool:
        return sys.platform.startswith("linux")

    @staticmethod
    def get_config_dir() -> Path:
        """OS標準の設定ファイルディレクトリを取得・作成"""
        path = Path(user_config_dir(APP_NAME, appauthor=False))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_cache_dir() -> Path:
        """OS標準のキャッシュディレクトリを取得・作成"""
        path = Path(user_cache_dir(APP_NAME, appauthor=False))
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def normalize_path_str(path: Path) -> str:
        """macOSのNFD濁点問題をNFCへ正規化し、API送信用にPOSIXスラッシュ形式へ統一"""
        posix_str = path.resolve().as_posix()
        return unicodedata.normalize("NFC", posix_str)

    @staticmethod
    @contextmanager
    def safe_temp_file(suffix: str = ".tmp", data: Optional[bytes] = None) -> Generator[Path, None, None]:
        """
        WindowsのNamedTemporaryFile排他ロック問題を回避する安全な一時ファイル生成。
        ファイルを一度閉じてからPathを渡し、スコープ終了時に確実に削除する。
        """
        fd, raw_path = tempfile.mkstemp(suffix=suffix)
        temp_path = Path(raw_path)
        try:
            if data is not None:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            else:
                os.close(fd)
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def resolve_executable(cmd: str) -> str:
        """
        Windowsでの .cmd / .exe 解決および macOS での PATH 補完を含めて実行ファイルパスを解決する。
        MCPサーバー (npx, uvx, python等) の起動時に必須。
        """
        resolved = shutil.which(cmd)
        if resolved:
            return resolved
        if PlatformCompat.is_macos():
            # macOS GUIアプリ等から呼ばれた場合のHomebrewパス補完
            extra_paths = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]
            resolved = shutil.which(cmd, path=os.pathsep.join(extra_paths))
            if resolved:
                return resolved
        raise FileNotFoundError(f"Executable '{cmd}' not found in PATH on {sys.platform}.")

    @staticmethod
    def resolve_ollama_base_url(configured_url: Optional[str] = None) -> str:
        """環境変数またはDockerコンテナ実行環境に応じたローカルOllama URLの透過解決"""
        if configured_url:
            return configured_url.rstrip("/")
        if env_url := os.environ.get("OLLAMA_HOST"):
            return env_url if env_url.startswith("http") else f"http://{env_url}"
        # Dockerコンテナ内からの実行判定 (/.dockerenv の存在チェック)
        if Path("/.dockerenv").exists():
            return "http://host.docker.internal:11434"
        return "http://127.0.0.1:11434"
```

## **9.4 パッケージ構造と依存関係の分離規約 (pyproject.toml)**

マルチプラットフォームでコンパイルエラーを起こさないための標準ディレクトリ構成と `pyproject.toml` 仕様です。

```
omni_ai/
├── __init__.py          # 公開API (OmniClient, BuiltinTool, 例外, 主要型)
├── compat.py            # OS差分吸収レイヤー (Windows/Linux/macOS対応)
├── config.py            # 環境変数・設定ファイルローダー
├── exceptions.py        # 統一例外階層 (OmniError等)
├── types.py             # Pydantic共通データモデル・マルチモーダル型
├── capabilities.py      # 全10領域のSync/Async抽象基底クラス
├── router.py            # OmniClient, FallbackChatAdapter, TelemetryTracker
├── tools.py             # HybridToolManager, @omni_tool デコレータ
└── providers/           # プロバイダ別アダプター実装 (遅延インポート対応)
    ├── openai.py
    ├── anthropic.py
    ├── gemini.py
    ├── ollama.py
    ├── jev.py
    ├── cohere.py
    ├── tavily.py
    ├── mistral_ocr.py
    ├── e2b_sandbox.py
    └── mcp_client.py
```

```
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "omni-ai-sdk"
version = "3.0.0"
description = "Cross-platform Multi-Provider Unified AI SDK"
readme = "README.md"
requires-python = ">=3.11"
# コア依存は100% Pure PythonでWindows/Linux/macOSどこでもバイナリビルド不要
dependencies = [
    "pydantic>=2.7.0",
    "httpx[http2]>=0.27.0",
    "platformdirs>=4.2.0",
    "anyio>=4.4.0",
]

[project.optional-dependencies]
jev = ["typesafe-sdk>=0.1.0"]
mcp = ["mcp>=1.0.0"]
sandbox = ["e2b-code-interpreter>=1.0.0"]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0", "respx>=0.21.0", "ruff>=0.6.0", "mypy>=1.11.0"]
```

## **9.5 開発者向けコーディング規約（禁止事項チェックリスト）**

実装担当者がプルリクエストを出す前に必ず守るべき6つの鉄則です：

8. **アダプター内で sys.platform や os.name を直接参照しない**（必ず `PlatformCompat` を呼ぶ）。  
9. **open() や Path.read\_text() / write\_text() を encoding="utf-8" なしで呼ばない**（Windowsの `cp932` クラッシュ防止）。  
10. **パス文字列を "/" や "\\\\" で手動結合しない**（必ず `Path` オブジェクトで操作する）。  
11. **tempfile.NamedTemporaryFile をそのまま別関数や外部コマンドに渡さない**（必ず `PlatformCompat.safe_temp_file()` を使う）。  
12. **外部CLI（npx, uvx, ffmpeg 等）を shutil.which 解決なしで subprocess 起動しない**（Windowsでの `.cmd` 未解決エラー防止）。  
13. **localhost を文字列リテラルでハードコードしない**（IPv6解決遅延やDocker内通信を考慮し、`127.0.0.1` または `PlatformCompat.resolve_ollama_base_url()` を使う）。

# **10\. まとめ**

v3.0仕様により、機能面の網羅性（全10領域のプロバイダ・ミックス）に加え、**非同期並列I/O・マルチモーダル入力・メモリ安全性・統一エラーと自動フェイルオーバー・横断コスト監視**という実運用に不可欠な要件がすべて満たされたプロダクション仕様となっています。