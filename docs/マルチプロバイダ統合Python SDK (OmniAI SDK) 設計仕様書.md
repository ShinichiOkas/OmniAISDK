# **マルチプロバイダ統合Python SDK (OmniAI SDK v3.1 \- Production Edition) 設計仕様書**

> **v3.1 (2026-11-07)**: v3.0 の不整合・欠落を修正した版。変更点の一覧は末尾の「11. 変更履歴」を参照。
> 実装上の詳細（モジュール構成、sans-IO 方式、プロバイダごとの対応表、テスト方針）は [OmniAI SDK 実装設計書 (コア＋Chat)](./OmniAI%20SDK%20実装設計書%20(コア＋Chat).md) に分けて書く。

# **1\. 概要と設計思想**

## **1.1 背景と目的**

現代の生成AI・エージェントシステムおよび高精度RAG（検索拡張生成）は、単一のプロバイダだけでは完結せず、役割ごとに最適な専門API・ローカルモデルを組み合わせるハイブリッド構成が主流となっています。

* 高度な対話・推論・ツール連携: [OpenAI API](https://developers.openai.com/api/reference/overview) や [Anthropic Claude API](https://platform.claude.com/docs/en/api/overview)  
* 超長文コンテキスト解析・マルチモーダル処理: [Google Gemini API](https://aistudio.google.com/api-reference)  
* ローカルGPU実行による機密保護・ゼロコスト運用: [Ollama](https://docs.ollama.com/api/introduction)  
* 高速・安価な型付き決定論的評価（System One）: [TypeSafe Jev](https://docs.typesafe.ai/introduction)  
* 高精度RAGおよび外部連携: [Cohere Rerank](https://cohere.com/rerank)、[Tavily Search](https://tavily.com/)、[E2B Sandbox](https://e2b.dev/)、MCP (Model Context Protocol)

本SDK（OmniAI SDK v3.1）は、全10領域のモダリティを適材適所でミックス利用できるだけでなく、**プロダクション運用に必須となる非同期I/O（`async/await`）、マルチモーダル型、統一例外階層と自動フェイルオーバー、メモリ安全なストリームI/O、およびコスト・レイテンシ監視（Observability）**を完全統合したエンタープライズ仕様を定義します。

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
| **6\. リランカー (Rerank)** | `RerankCapability` | Cohere Rerank, Jina, Voyage, ローカルBGE-Reranker | `query` と `documents` から関連度順スコア付きリストへの再整列 | 多言語モデル指定、チャンク最大トークン調整 |
| **7\. Web検索 (Search)** | `SearchCapability` | Tavily, Exa, Brave Search, Google Grounding | `query`, `max_results` からタイトル・URL・本文スニペットの配列返却 | ドメインフィルタ、生HTML取得、日付範囲指定 |
| **8\. 文書解析 (DocParser)** | `DocumentParserCapability` | Mistral OCR, LlamaParse, Unstructured, ローカルMarker | `MediaSource` (PDF/Office) から構造化Markdownおよび表データの抽出 | 画像・図表切り出し、ページ単位メタデータ付与 |
| **9\. コード実行 (Sandbox)** | `SandboxCapability` | E2B Sandbox, Modal, Gemini Code Execution, ローカルDocker | Pythonコード文字列の隔離実行と標準出力・エラー・生成ファイルの取得 | 永続セッション維持、パッケージ動的インストール |
| **10\. 外部接続 (MCP)** | `MCPCapability` | Anthropic MCP準拠サーバー全般 (stdio / SSE) | MCPサーバーからの `ToolDefinition` 自動取得とツール実行のブリッジ | リソース購読、プロンプトテンプレート取得 |

# **3\. 統一例外階層とエラーハンドリング仕様 (`omni_ai/exceptions.py`)**

呼び出し側がプロバイダ固有のSDK例外に依存しないよう、すべてのアダプターは以下の標準例外に変換して送出します。

`retryable` は「**同じ要求をそのまま送り直せば成功しうるか**」だけを表します。別プロバイダへ切り替えるかどうか（フォールバック）は、`FallbackChatAdapter.fallback_on` で例外の型ごとに別途決めます（§6）。

```py
class OmniError(Exception):
    """OmniAI SDK すべての例外の基底クラス"""
    def __init__(
        self,
        message: str,
        provider: str,
        model: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
        raw_error: Exception | None = None,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retryable = retryable
        self.raw_error = raw_error      # APIキーを含むヘッダは保持しない
        self.request_id = request_id

class AuthenticationError(OmniError):
    """APIキー不正 (401、および Gemini の 400 API_KEY_INVALID) - リトライ不可"""

class PermissionDeniedError(AuthenticationError):
    """権限不足・地域制限 (403) - リトライ不可"""

class InvalidRequestError(OmniError):
    """要求不正 (400) - リトライ不可"""

class ContextLengthExceededError(InvalidRequestError):
    """最大コンテキスト長超過 - リトライ不可（fallback_on に加えれば長文脈プロバイダへ逃がせる）"""

class UnsupportedFeatureError(InvalidRequestError):
    """要求された機能（組み込みツール・入力モダリティ等）をこのアダプター/モデルが扱えない。SDK が送信前に検出"""

class NotFoundError(OmniError):
    """モデル・ファイル等が存在しない (404) - リトライ不可"""

class QuotaExceededError(OmniError):
    """課金残高・利用枠の枯渇 (Gemini 402 等) - リトライ不可・フォールバック対象"""

class RateLimitError(OmniError):
    """レート制限到達 (429) - リトライおよびフォールバック対象"""
    def __init__(self, *args, retry_after_seconds: float | None = None, **kwargs):
        super().__init__(*args, retryable=True, **kwargs)
        self.retry_after_seconds = retry_after_seconds

class ProviderOverloadedError(OmniError):
    """プロバイダ過負荷・一時障害 (500/502/503/504/529) - リトライおよびフォールバック対象"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, retryable=True, **kwargs)

class ProviderConnectionError(OmniError):
    """接続拒否・DNS失敗など（ローカル Ollama 未起動の典型）- リトライおよびフォールバック対象"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, retryable=True, **kwargs)

class ProviderTimeoutError(ProviderConnectionError):
    """タイムアウト - リトライおよびフォールバック対象"""

class StreamInterruptedError(OmniError):
    """ストリームの途中（最初のイベント送出後）での失敗。フォールバックしない (ADR-07)"""

class SchemaValidationError(OmniError):
    """Structured Outputs または Judge の型検証失敗"""

class ToolLoopLimitError(OmniError):
    """ToolRunner が max_turns に達した。途中結果を partial_result に保持"""

class CapabilityNotConfiguredError(OmniError):
    """OmniClient で未設定の Capability にアクセスした"""
```

# **4\. 共通データモデル・テレメトリ・マルチモーダル型 (`omni_ai/types.py`)**

```py
import inspect
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Literal, Optional, Type, Union
from pydantic import BaseModel, ConfigDict, Field, create_model

# -------------------------------------------------------------
# 0. 共通テレメトリ & メディアソース抽象化
# -------------------------------------------------------------
FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error", "unknown"]

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0      # 思考トークン (Gemini thoughtsTokenCount 等)
    total_tokens: int = 0

class ResponseMeta(BaseModel):
    """全モダリティ共通の追跡・コスト計算メタデータ"""
    provider: str
    model: str
    latency_ms: float = 0.0
    time_to_first_event_ms: Optional[float] = None   # ストリーミング時のみ
    estimated_cost_usd: Optional[float] = None       # 単価不明なら None（0 と区別する。ADR-10）
    request_id: Optional[str] = None
    usage: Usage = Field(default_factory=Usage)
    attempts: int = 1                                # 再試行を含む試行回数
    fallback_from: List[str] = Field(default_factory=list)   # 失敗して飛ばしたプロバイダ名

class FileRef(BaseModel):
    """プロバイダ側に事前アップロードされたファイル参照 (Gemini File API / OpenAI Files等)"""
    provider: str                  # どのプロバイダの参照か（他社アダプターには渡さない）
    file_id: str
    uri: Optional[str] = None
    mime_type: str

class UrlRef(BaseModel):
    """URL で指定するメディア。SDK は自動ダウンロードしない（URL 入力非対応のプロバイダでは UnsupportedFeatureError）"""
    url: str

# メモリ圧迫を防ぐ汎用メディア入力型 (bytes, ファイルパス, ストリーム, アップロード済み参照, URL)
# v3.0 の素の `str` は「パスか URL か base64 か」が曖昧なため除外し、UrlRef に置き換えた
MediaSource = Union[bytes, Path, BinaryIO, FileRef, UrlRef]

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
    model_config = ConfigDict(arbitrary_types_allowed=True)   # BinaryIO を許すため
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

class OmniTool(ToolDefinition):
    """@omni_tool が返す、呼び出し可能なツール定義。元の関数を handler として保持する (ADR-12)。
    v3.0 では関数が ToolDefinition に置き換わり、Layer 1 のローカル実行で元の関数を呼べなかった。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    handler: Callable[..., Any] = Field(exclude=True)
    is_async: bool = False

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler(*args, **kwargs)

def omni_tool(func: Optional[Callable] = None, *, name: Optional[str] = None,
              description: Optional[str] = None, strict: bool = True):
    """Python関数から OmniTool を自動生成するデコレータ（@omni_tool / @omni_tool(name=...) の両形式）。
    - 引数スキーマは pydantic.create_model で生成し、プロバイダが受け付ける JSON Schema サブセットへ正規化する
      （title 除去・$defs 展開。詳細は実装設計書 §5.3）
    - self / cls / *args / **kwargs を含む関数は TypeError
    - Docstring の Args: 節を各引数の description に写す"""
    ...

class BuiltinTool(str, Enum):
    WEB_SEARCH = "builtin::web_search"
    CODE_EXECUTION = "builtin::code_execution"

ToolSpec = Union[ToolDefinition, OmniTool, BuiltinTool]   # tools 引数の要素型

class ToolCall(BaseModel):
    id: str                       # プロバイダが id を返さない場合は SDK が採番する
    name: str
    arguments: Dict[str, Any]
    provider_state: Dict[str, Any] = Field(default_factory=dict)   # 例: Gemini thoughtSignature (ADR-11)

class ChatMessage(BaseModel):
    role: Role
    content: Union[str, List[ContentPart]]
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None    # TOOL メッセージのツール名（Gemini / Ollama は名前で結果を対応づける）
    provider_state: Dict[str, Any] = Field(default_factory=dict)   # アダプターだけが読み書きする不透明な状態 (ADR-11)

class Citation(BaseModel):
    """組み込み Web 検索 (Layer 2) の出典"""
    url: str
    title: Optional[str] = None
    start_index: Optional[int] = None
    end_index: Optional[int] = None

class ChatResponse(BaseModel):
    content: Optional[str] = None
    parsed: Optional[Any] = None
    reasoning: Optional[str] = None          # 思考の要約（取得できるプロバイダのみ）
    tool_calls: List[ToolCall] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    finish_reason: FinishReason = "stop"
    meta: ResponseMeta
    provider_state: Dict[str, Any] = Field(default_factory=dict)   # アシスタントメッセージへ引き継ぐ不透明な状態 (ADR-11)

    def to_message(self) -> ChatMessage:
        """履歴にそのまま積めるアシスタントメッセージ（tool_calls と provider_state を保持）を返す"""
        ...

class StreamEvent(BaseModel):
    """ストリーミング中の構造化イベント（テキスト・思考・ツール呼び出し・出典・完了時統計を統合）
    v3.0 の "tool_call_delta" は「delta と名乗りながら完成した ToolCall を運ぶ」矛盾があったため "tool_call" に改名。
    引数 JSON の断片はアダプター内部で組み立て、利用者には完成した ToolCall だけを渡す。"""
    type: Literal["text_delta", "reasoning_delta", "tool_call", "citation", "message_complete"]
    delta_text: str = ""
    tool_call: Optional[ToolCall] = None
    citation: Optional[Citation] = None
    finish_reason: Optional[FinishReason] = None
    response: Optional[ChatResponse] = None   # message_complete 時: 全体を組み立てた ChatResponse
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
    normalized: bool = False   # v3.1: L2 正規化済みか（embed(normalize=True) が既定）

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

v3.1 での共通規約:
- すべての Capability は共通基底 `Capability` を継承し、`provider_name`（v3.0 §8.4 が参照していたのに未定義だった）と、HTTP セッションを閉じる `close()` / `aclose()`、コンテキストマネージャを持つ。
- `messages` / `prompt` などの主入力より後ろの引数は**キーワード専用**（`*,`）。位置引数のずれがフォールバック先で別の意味になる事故を防ぐ。
- v3.0 の `**kwargs` は `**provider_options` とし、プロバイダ固有の指定は `gemini={...}` / `ollama={...}` のように**名前空間付き**で渡す。各アダプターは自分の名前空間だけを読み、他は無視する（無視した事実は debug ログに出す）。これにより、フォールバック先に他社向けの指定が混入しない。
- 以下では Chat を v3.1 の形で示す。Chat 以外の 9 領域のシグネチャは、上記の共通規約を当てはめる以外は v3.0 のまま（Chat 以外の設計は実装着手時に見直す）。

```py
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, ClassVar, Dict, FrozenSet, Iterator, List, Literal, Optional, Sequence, Type, Union
from pydantic import BaseModel
from .types import (
    BuiltinTool, ChatMessage, ChatResponse, StreamEvent, ToolDefinition, ToolSpec,
    JudgeQuestion, JudgeResponse, ImageResponse,
    AudioSpeechResponse, AudioTranscriptionResponse, EmbeddingResponse,
    RerankResponse, SearchResponse, ParsedDocument, SandboxExecutionResult, MediaSource
)

class Capability(ABC):
    provider_name: ClassVar[str]

    def close(self) -> None: ...
    async def aclose(self) -> None: ...
    def __enter__(self): return self
    def __exit__(self, *exc) -> None: self.close()
    async def __aenter__(self): return self
    async def __aexit__(self, *exc) -> None: await self.aclose()


ToolChoice = Union[Literal["auto", "none", "required"], str]   # str はツール名の強制指定

class ChatCapability(Capability):
    # このアダプターがサーバー側で実行できる組み込みツール (ADR-09)
    # ClassVar ではなく、ラッパー（FallbackChatAdapter・計測プロキシ）がインスタンスで上書きできる属性
    supported_builtins: FrozenSet[BuiltinTool] = frozenset()

    @abstractmethod
    def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[Sequence[ToolSpec]] = None,
        tool_choice: ToolChoice = "auto",
        response_schema: Optional[Type[BaseModel]] = None,
        timeout: Optional[float] = None,
        **provider_options: Any,
    ) -> ChatResponse:
        pass

    @abstractmethod
    async def agenerate(self, messages: Sequence[ChatMessage], *, ...) -> ChatResponse:   # 引数は generate と同一
        pass

    @abstractmethod
    def stream(self, messages: Sequence[ChatMessage], *, ...) -> Iterator[StreamEvent]:      # 引数は generate と同一
        pass

    @abstractmethod
    def astream(self, messages: Sequence[ChatMessage], *, ...) -> AsyncIterator[StreamEvent]:  # 引数は generate と同一
        pass


class JudgeCapability(Capability):
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


class ImageCapability(Capability):
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


class AudioCapability(Capability):
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


class EmbeddingCapability(Capability):
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


class RerankCapability(Capability):
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


class SearchCapability(Capability):
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


class DocumentParserCapability(Capability):
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


class SandboxCapability(Capability):
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


class MCPCapability(Capability):
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

**再試行とフォールバックの順序 (ADR-08)**: まず同一プロバイダ内で `RetryPolicy`（transport 層、指数バックオフ＋ジッタ、`retry_after` 優先）により再試行し、それでも失敗した場合にルーター層の `FallbackChatAdapter` が次のプロバイダへ切り替えます。

**ストリーミング中のフォールバック (ADR-07)**: 最初の `StreamEvent` を返す前の失敗に限り次のプロバイダへ切り替えます。1 つでもイベントを返した後の失敗は `StreamInterruptedError` とし、切り替えません（2 社の出力が混ざった応答を防ぐ）。

**計測の一元化**: 時間計測・コスト計算・`on_response` フックの呼び出しはアダプターではなく `OmniClient` が行います。`OmniClient.chat` などのプロパティは、設定されたアダプターを計測用プロキシで包んで返します（v3.0 では `on_response` を受け取りながら呼び出す経路が無かった）。

```py
from typing import Callable, List, Mapping, Optional, Sequence, Tuple, Type
from .exceptions import (
    OmniError, RateLimitError, ProviderOverloadedError, ProviderConnectionError,
    QuotaExceededError, CapabilityNotConfiguredError,
)
from .types import ResponseMeta
from .pricing import PricingTable
from .capabilities import (
    ChatCapability, JudgeCapability, ImageCapability, AudioCapability,
    EmbeddingCapability, RerankCapability, SearchCapability,
    DocumentParserCapability, SandboxCapability, MCPCapability
)

DEFAULT_FALLBACK_ON: Tuple[Type[OmniError], ...] = (
    RateLimitError, ProviderOverloadedError, ProviderConnectionError, QuotaExceededError,
)

class FallbackChatAdapter(ChatCapability):
    """プライマリが fallback_on に含まれる例外で失敗した際、次のプロバイダへ切り替えるラッパー。
    generate / agenerate / stream / astream の 4 メソッドすべてを実装する。"""
    provider_name = "fallback"

    def __init__(
        self,
        providers: Sequence[ChatCapability],
        *,
        fallback_on: Tuple[Type[OmniError], ...] = DEFAULT_FALLBACK_ON,
        # model 引数の読み替え表: {provider_name: {要求された model: そのプロバイダでの model}}
        # 対応が無いプロバイダでは、そのアダプターの default_model を使う
        model_map: Optional[Mapping[str, Mapping[str, str]]] = None,
    ):
        if not providers:
            raise ValueError("At least one provider is required.")
        self.providers = list(providers)
        self.fallback_on = fallback_on
        self.model_map = model_map or {}

    async def agenerate(self, messages, *, model=None, tools=None, **kwargs):
        errors: List[OmniError] = []
        for provider in self.providers:
            if not _supports_requested_builtins(provider, tools):   # 組み込みツール未対応のプロバイダは飛ばす
                continue
            try:
                res = await provider.agenerate(
                    messages, model=self._map_model(provider, model), tools=tools, **kwargs)
                res.meta.fallback_from = [e.provider for e in errors]
                return res
            except self.fallback_on as e:
                errors.append(e)
                continue
        # 全滅: 最後の例外を送出し、それまでの例外を add_note で要約して付ける
        ...

    # generate は同形。stream / astream は「最初のイベントを返す前」だけ切り替える (ADR-07)


class TelemetryTracker:
    """複数プロバイダを跨いだAPI呼び出しの累計コストとレイテンシを集計（スレッド安全）"""
    def __init__(self):
        self.history: List[ResponseMeta] = []
        self._lock = threading.Lock()

    def record(self, meta: ResponseMeta) -> None:
        with self._lock:
            self.history.append(meta)

    @property
    def total_cost_usd(self) -> float:
        """単価既知の呼び出しの合計。単価不明 (None) は含めず unpriced_calls で別に数える"""
        return sum(m.estimated_cost_usd for m in self.history if m.estimated_cost_usd is not None)

    @property
    def unpriced_calls(self) -> int:
        return sum(1 for m in self.history if m.estimated_cost_usd is None)

    @property
    def total_latency_ms(self) -> float:
        return sum(m.latency_ms for m in self.history)

    def by_provider(self) -> dict: ...


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
        on_response: Optional[Callable[[ResponseMeta], None]] = None,   # 例外を出しても本処理は失敗させない（warning ログのみ）
        pricing: Optional[PricingTable] = None,   # 単価表（注入式、ADR-10）。未指定なら Ollama 等の無料プロバイダ以外は None
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
        await self.aclose()

    async def aclose(self) -> None:
        """設定済みの全 Capability の aclose() を呼び、HTTP セッション (httpx.AsyncClient 等) を閉じる。
        1 つが失敗しても残りは閉じる（失敗は ExceptionGroup でまとめて送出）"""
        ...

    def __enter__(self) -> "OmniClient": return self
    def __exit__(self, *exc) -> None: self.close()
    def close(self) -> None: ...   # 同期版

    @property
    def chat(self) -> ChatCapability:
        if not self._chat:
            raise CapabilityNotConfiguredError("Chat provider is not configured.", provider="omni")
        return self._instrumented(self._chat)   # 計測プロキシで包んで返す

    @property
    def tools(self) -> "ToolRunner":
        """chat / search / sandbox から組み立てたクライアント側ツールループ (§8.4)"""
        ...

    @property
    def judge(self) -> JudgeCapability:
        if not self._judge:
            raise CapabilityNotConfiguredError("Judge provider is not configured.", provider="omni")
        return self._judge

    @property
    def image(self) -> ImageCapability:
        if not self._image:
            raise CapabilityNotConfiguredError("Image provider is not configured.", provider="omni")
        return self._image

    @property
    def audio(self) -> AudioCapability:
        if not self._audio:
            raise CapabilityNotConfiguredError("Audio provider is not configured.", provider="omni")
        return self._audio

    @property
    def embedding(self) -> EmbeddingCapability:
        if not self._embedding:
            raise CapabilityNotConfiguredError("Embedding provider is not configured.", provider="omni")
        return self._embedding

    @property
    def rerank(self) -> RerankCapability:
        if not self._rerank:
            raise CapabilityNotConfiguredError("Rerank provider is not configured.", provider="omni")
        return self._rerank

    @property
    def search(self) -> SearchCapability:
        if not self._search:
            raise CapabilityNotConfiguredError("Search provider is not configured.", provider="omni")
        return self._search

    @property
    def doc_parser(self) -> DocumentParserCapability:
        if not self._doc_parser:
            raise CapabilityNotConfiguredError("DocumentParser provider is not configured.", provider="omni")
        return self._doc_parser

    @property
    def sandbox(self) -> SandboxCapability:
        if not self._sandbox:
            raise CapabilityNotConfiguredError("Sandbox provider is not configured.", provider="omni")
        return self._sandbox

    @property
    def mcp(self) -> MCPCapability:
        if not self._mcp:
            raise CapabilityNotConfiguredError("MCP provider is not configured.", provider="omni")
        return self._mcp
```

# **7\. 統合実装サンプル（非同期並列実行・フォールバック・マルチモーダル・コスト集計）**

> **v3.1 注記**: 最初に実装するのは Chat の Gemini / Ollama アダプターである（実装設計書）。その他のアダプター（Jev・Cohere・Tavily・Mistral OCR・Ollama 画像生成）は将来の実装予定で、このサンプルは全体像を示すためのものである。v3.0 のサンプルにあったモデル名（`gpt-4o`、`claude-3-5-sonnet-latest`）は古いため、Gemini ➔ Ollama の構成に差し替えた。

以下のコードは、**非同期並列処理 (`asyncio.gather`)、Gemini ➔ ローカルOllama 自動フェイルオーバー、Pathオブジェクトによるメモリ安全なPDF解析、Jevによる高速判定、ローカルOllamaでの画像生成、および総コスト追跡** を組み合わせたプロダクション実装例です。

```py
import asyncio
from pathlib import Path
from omni_ai import OmniClient, FallbackChatAdapter
from omni_ai.providers import (
    GeminiChatAdapter,       # プライマリ対話: Google Gemini
    OllamaChatAdapter,       # バックアップ対話: ローカル Ollama
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
    # 1. Gemini がレート制限・障害・残高切れの時は自動でローカル Ollama へフォールバックする構成
    resilient_chat = FallbackChatAdapter([
        GeminiChatAdapter(default_model="gemini-3.1-flash-lite"),
        OllamaChatAdapter(default_model="qwen3:8b"),   # tools 対応のローカルモデルを指定する（例）
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

> **v3.1 注記**: Gemini 列と Ollama 列は、2026-11-07 に一次資料で確認して修正した（確認元は実装設計書 §12）。OpenAI 列と Anthropic 列は v3.0 の記述のままで、**未検証**（各アダプターを実装するときに確認する）。

| 比較項目 | OpenAI API | Anthropic Claude API | Google Gemini API | Ollama API |
| :---- | :---- | :---- | :---- | :---- |
| **自作ツールの実行方式(Layer 1\)** | クライアント実行type: "function" を定義し、返ってきた引数をアプリが実行して結果を再送信。 | クライアント実行tools に定義し、tool\_use ブロックを受信後、tool\_result を再送信。 | クライアント実行function\_declarations を定義し、function\_call を受けて function\_response を再送信。 | クライアント実行tools を指定し、返ってきた tool\_calls をアプリがローカルで実行して再送信。 |
| **サーバー側Web検索(Layer 2\)** | 提供ありResponses APIの組み込み web\_search。OpenAI側が検索して引用元付きで回答。 | 提供ありMessages APIの組み込み web\_search (Server Tools)。Anthropic側が自律実行。 | 提供ありGoogle Search Grounding (`{"google_search": {}}`)。Google検索エンジンが直接実行。自作関数との併用は Gemini 3 以降の Preview 機能で、`toolConfig.includeServerSideToolInvocations: true` が必要。 | ローカル /api/chat にはなし（ツールは function 型のみで、実行は常にクライアント側）。別途 ollama.com の Web Search API（`POST https://ollama.com/api/web_search`、APIキー必須）があり、クライアントが function ツールとして橋渡しすれば使える。 |
| **サーバー側コード実行(Layer 2\)** | 提供ありcode\_interpreter。クラウドコンテナでPythonを実行し、グラフや計算結果を返却。 | 提供ありServer Toolsの code\_execution。Anthropicのサンドボックス環境でPythonを実行。 | 提供ありCode Execution (`{"code_execution": {}}`)。GoogleのPythonサンドボックスで自律計算（1 回最大 30 秒）。 | なし(ローカル端末での実行となり、安全対策はアプリ側の責任) |
| **その他のサーバー機能** | ・file\_search (OpenAI側RAG)・image\_generation (DALL-E) | ・web\_fetch (URL先コンテンツ取得)・(特殊) computer\_use (指示はモデル、実行はクライアント) | ・Google Maps連携 (google\_maps)・指定URLの事前解析 (url\_context) | なし |
| **リモートMCP連携(Layer 3\)** | 対応Responses APIからリモートMCPサーバーへ直接接続可能。 | ネイティブ対応Messages APIからリモートMCPツールを直接ロード・実行可能。 | SDK/エコシステム対応クライアントSDKやエージェントフレームワークで連携。 | クライアント側対応LangChain等の上位レイヤー側でMCPツールをバインド。 |
| **エージェントループ** | Responses APIが自動管理(1リクエスト内で複数ツールをサーバー側で自律反復) | Server Tools時はサーバー側で自動完結(クライアントツールの場合はアプリ側でループ) | サーバー側で自律実行(SearchやCode Executionは1ターンで内部完結) | 100% クライアント側でループ |

## **8.3 各社の特徴とアーキテクチャ上の注意点**

**OpenAI:**  
従来の /v1/chat/completions ではクライアントサイド実行（Layer 1）のみをサポート。  
最新の /v1/responses（およびAssistants API）では、web\_search や code\_interpreter などの組み込みツールがプロバイダ側（Layer 2）で実行可能。自作関数（type: "function"）と組み込みツールを同一配列で渡せ、組み込みツール実行時はOpenAI側でループを自動処理。  
**Google Gemini:**  
Googleの検索インフラと直結した Google Search Grounding（google\_search）により、最新情報の引用メタデータ付き取得が極めて低遅延。  
code\_execution を有効化すると、数学問題やデータ集計に対してGemini自身が裏でPythonコードを生成・Googleクラウドサンドボックスで実行・エラー自己修正を行い、計算結果を返却。  
**（v3.1 追記・重要）** Gemini 3 / 4 の関数呼び出しでは、モデル応答に含まれる `thoughtSignature` を次のターンで元の part に付けたまま返さないと HTTP 400 になる。アダプターはモデル応答の parts を加工せずに保持し、そのまま再送する（`ChatMessage.provider_state`、ADR-11）。また、Gemini の公式索引は `generateContent` を legacy、Interactions API を既定と位置づけている（generateContent 自体は引き続きサポート）。  
**Anthropic (Claude):**  
最新仕様で web\_search や code\_execution を Server Tools（Anthropicのサーバー側で自動ループ実行されるツール）として正式導入。  
Computer Use (computer\_20241022) は、ツールスキーマ定義はAnthropicが事前提供するが、実際のOS画面操作やVM実行はクライアント側（Docker等）で走らせるハイブリッド設計。  
**Ollama:**  
完全ローカル・オフライン動作のため、クラウド側のマネージドツール（Google検索やホスト型コンテナ）は一切持たない。  
モデルが引数JSONを出力した後は、100%クライアント側（SDKまたはアプリ）が外部検索APIやローカルDockerを実行して結果を差し戻すエージェントループを回す必要がある。  
**（v3.1 追記）** ただし、名前が `:cloud` / `-cloud` で終わるモデルは、ローカルの Ollama を経由して ollama.com 上で実行される（無料ではなく、オフラインでも動かない）。また `/api/chat` は、既定で文脈長 `num_ctx` を超えた古い履歴を**黙って切り捨てる**（エラーにならない）。

## **8.4 組み込みツールの解決とクライアント側ツールループ (omni\_ai/tools.py)**

v3.0 の `HybridToolManager` は v3.1 で次のように置き換えた。v3.0 の問題点は、①`if provider_name == ...` の分岐がツール側に集中していた、②`provider_name` が `ChatCapability` に定義されていなかった、③ツール結果を差し戻す前に、アシスタントの `tool_calls` メッセージを履歴に足していなかった、④同期の handler を非同期ループの中で直接呼んでいた、⑤未知のツール名を黙って無視していた、⑥`max_turns` に達しても黙って返していた、⑦Gemini 用のツール JSON（`{"type": "google_search"}`）が誤っていた、の 7 点である。

**(1) 組み込みツールはアダプターが自己申告する (ADR-09)**  
各 `ChatCapability` は `supported_builtins` で、サーバー側で実行できる `BuiltinTool` を宣言し、ネイティブのツール JSON への変換はアダプターの中で行う。

| BuiltinTool | Gemini | Ollama |
| :---- | :---- | :---- |
| `WEB_SEARCH` | `{"google_search": {}}` | 非対応 |
| `CODE_EXECUTION` | `{"code_execution": {}}` | 非対応 |

非対応の組み込みツールを `agenerate(tools=[...])` に直接渡すと、`UnsupportedFeatureError` になる。

**(2) 代行が必要なら ToolRunner を明示的に使う**

```py
from typing import Any, Literal, Sequence
from .types import ChatMessage, ChatResponse, ResponseMeta, ToolSpec

class ToolRunner:
    """クライアント側ツールループ。非対応の BuiltinTool は search / sandbox Capability による OmniTool に置き換えて代行する"""
    def __init__(self, chat, *, search=None, sandbox=None, max_turns: int = 8,
                 parallel_tool_calls: bool = True,
                 on_tool_error: Literal["return_to_model", "raise"] = "return_to_model"): ...
    def run(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], **opts) -> "ToolRunResult": ...
    async def arun(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], **opts) -> "ToolRunResult": ...

class ToolRunResult(BaseModel):
    response: ChatResponse          # 最後の応答
    messages: List[ChatMessage]     # ループ中に追加されたものを含む全履歴
    turns: int
    metas: List[ResponseMeta]
```

ループの手順:
1. `BuiltinTool` を解決する（`supported_builtins` にあればそのまま渡し、無ければ代行用の `OmniTool` に置き換える。代行に必要な Capability が無ければ `UnsupportedFeatureError`）
2. `agenerate` を呼ぶ。`tool_calls` が空なら終了する
3. `response.to_message()`（`tool_calls` と `provider_state` を含む）を履歴に足す
4. 各 `ToolCall` の handler を実行する（同期関数はスレッドで実行し、`parallel_tool_calls=True` なら並行して実行する）。未知の名前は「unknown tool」という結果を、引数の検証失敗や例外は `on_tool_error` に従った結果を返す
5. `Role.TOOL`・`tool_call_id`・`name` を付けて結果を履歴に足し、2 に戻る（handler を持たない `ToolDefinition` が呼ばれた場合は、ループを止めて `ToolRunResult.pending_tool_calls` として呼び出し側に返す）
6. `max_turns` に達したら `ToolLoopLimitError`（途中の結果を保持する）を送出する

## **8.5 利用者側のメリット**

```py
# Gemini: サーバー側（Google検索）で実行される
response = await ai.chat.agenerate(
    messages=[ChatMessage(role=Role.USER, content="2026年最新のAI動向を調べて")],
    tools=[BuiltinTool.WEB_SEARCH],
)

# プロバイダを問わず同じ書き方: Gemini ではネイティブ実行、Ollama では SDK が SearchCapability (例: Tavily) で代行してループする
result = await ai.tools.arun(
    messages=[ChatMessage(role=Role.USER, content="2026年最新のAI動向を調べて")],
    tools=[BuiltinTool.WEB_SEARCH, calculate_roi],
)
print(result.response.content)
```

v3.0 は「`agenerate` に渡すだけで、Ollama でも裏で検索が走る」としていたが、v3.1 ではやめた。`agenerate` が裏で何ターンも呼び出しを重ねると、1 回の呼び出しのコスト・レイテンシ・履歴の意味が、プロバイダによって変わってしまうためである。複数ターンになりうる処理は `ai.tools.arun()` に集める。

# **9\. マルチプラットフォーム（Windows / Linux / macOS）実装規約とアーキテクチャ決定記録 (ADR)**

Windows、Linux、macOS の3大OS環境（およびDocker/WSL2環境）で一切の挙動差異やビルドエラーを起こさせず、開発者が実装時に迷わないための**アーキテクチャ決定記録（ADR: Architecture Decision Records）**と**OS差分吸収レイヤー仕様**を定義します。

## **9.1 実装におけるアーキテクチャ決定事項 (ADR)**

| ADR番号 | 決定テーマ | 採用方針（結論） | 理由・背景 |
| :---- | :---- | :---- | :---- |
| **ADR-01** | **OS差分の隔離場所** | **omni\_ai/compat.py 単一モジュールに完全隔離**（各アダプター内での `sys.platform` や `os.name` 分岐を全面禁止） | アダプターやビジネスロジック内にOS判定 (`if sys.platform == "win32":`) が散在するとテスト漏れや保守破綻を招くため、OS固有処理はすべて `compat` レイヤーの関数経由で呼び出す。 |
| **ADR-02** | **HTTP通信基盤の統一** | **httpx \+ anyio ベースのPure Python実装を標準採用**（各社公式SDKへのハード依存を排除） | 全10社の公式SDKを必須依存にするとPydanticやgRPC/C拡張のバージョン競合が起き、特にWindows環境でC++ビルドツール不足による `pip install` 失敗が多発するため。**（v3.1 明確化）** 禁止するのは「コア依存」と「HTTP で直接叩けるプロバイダの公式SDK」への依存。プロトコルの参照実装が SDK そのものであるもの（MCP）や、公開 HTTP 仕様が無いもの（E2B・Jev 等）は、**任意依存 (extras) に限って**公式SDKを使ってよい（§9.4 の optional-dependencies はこの例外にあたる）。 |
| **ADR-03** | **パッケージ配布形態** | **Pure Python Wheel (py3-none-any.whl) の保証**コア依存は `pydantic`, `httpx`, `platformdirs`, `anyio` の4つのみ | OS固有のネイティブバイナリ（Rust/C++コンパイル）をコアから排除し、Windows / Linux (x86\_64, ARM64) / macOS (Intel, Apple Silicon) すべてで単一Wheelが即時インストールできるようにする。 |
| **ADR-04** | **設定と認証の解決順序** | **①引数明示 ➔ ②OMNI\_\*環境変数 ➔ ③各社標準環境変数 ➔ ④OS標準設定ファイル** の厳格な4段階優先順位 | 開発環境（`.env` / 設定ファイル）と本番コンテナ環境（環境変数注入）でコード変更なしに挙動を統一するため。 |
| **ADR-05** | **テストマトリクス基準** | **GitHub Actions 等での3 OS × Python 3.11+ マトリクスCI必須化** | パス区切り文字やファイルロック、文字コードのバグをPR段階で機械的に検出するため。 |
| **ADR-06** | **Sync / Async 二重実装の作り方** (v3.1) | **sans-IO 方式**: 要求組み立て・応答解釈を I/O の無い純関数 (codec) に寄せ、`httpx.Client` / `httpx.AsyncClient` の薄い transport 2 本から呼ぶ。同期版を `asyncio.run()` で作らない | 既にイベントループが走っている環境 (Jupyter 等) でも同期 API が壊れない。代償は transport が 2 本になること。 |
| **ADR-07** | **ストリーミング中のフォールバック** (v3.1) | 最初のイベント送出前に限り切り替える。送出後の失敗は `StreamInterruptedError` | 2 社の出力が混ざった応答を黙って返さないため。代償は途中切断時に呼び出し側の再試行が要ること。 |
| **ADR-08** | **再試行とフォールバックの順序** (v3.1) | 同一プロバイダ内の再試行 (`RetryPolicy`、transport 層) → 次プロバイダへのフォールバック (router 層) | 一時的な 429/503 で即座に別社へ移るとコストと品質が変わるため。代償は最悪待ち時間の増加 (既定の試行回数を 2 と小さくする)。 |
| **ADR-09** | **組み込みツールの解決** (v3.1) | アダプターが `supported_builtins` で自己申告し、変換もアダプター内で行う。代行ループは `ToolRunner` で明示的に行う | ツール側に `if provider_name == ...` 分岐を集めないため。代償は v3.0 §8.5 の「agenerate に渡すだけで自動代行」をやめたこと。 |
| **ADR-10** | **コスト推定** (v3.1) | 単価表 `PricingTable` を注入する方式。単価不明は `None`（0 にしない）。SDK に単価表は同梱しない | 古い単価が黙って使われ続けるのを防ぐため。代償は単価表の保守が利用者側になること。 |
| **ADR-11** | **プロバイダ固有の不透明な状態** (v3.1) | `ChatMessage.provider_state` / `ToolCall.provider_state` に保持し、アダプターだけが読み書きする | Gemini 3 以降の thought signature を欠落させずに次ターンへ渡すため（欠落すると HTTP 400）。 |
| **ADR-12** | **`@omni_tool` の戻り値** (v3.1) | 元の関数を `handler` として保持する呼び出し可能な `OmniTool` を返す | v3.0 では関数が `ToolDefinition` に置き換わり、ローカル実行できなかったため。 |

## **9.2 OS差分が発生する5つの領域と標準吸収ルール**

| 領域 | Windows 特有の挙動・課題 | Linux 特有の挙動・課題 | macOS 特有の挙動・課題 | SDKでの標準吸収ルール（omni\_ai/compat） |
| :---- | :---- | :---- | :---- | :---- |
| **1\. ファイルパスと文字コード** | ・バックスラッシュ (`\`) 区切り・標準エンコーディングが `cp932` (Shift-JIS)・開いている一時ファイルを別プロセスから開けない排他ロック仕様 | ・スラッシュ (`/`) 区切り・大文字小文字を厳密に区別 (Case-sensitive)・UTF-8標準 | ・スラッシュ (`/`) 区切り・デフォルトAPFSは大文字小文字を区別しない・ファイル名濁点のNFD正規化問題 | ・文字列連結や `os.path` を禁止し **`pathlib.Path` に完全統一**・全ファイルI/Oで `encoding="utf-8"` を強制・ファイル名比較時は `unicodedata.normalize("NFC", name)` を適用・一時ファイルは閉じてからパスを渡す共通コンテキストマネージャを使用 |
| **2\. ローカルAI・GPUバックエンド** | ・NVIDIA CUDA または DirectML・OllamaのMLX系画像生成モデル (`x/flux2-klein`等) が非対応の場合あり | ・NVIDIA CUDA / AMD ROCm・サーバー用途ではVRAM管理が厳格 | ・Apple Silicon Unified Memory (Metal / MLX)・OllamaのMLX画像生成がネイティブ高速稼働（**v3.1 注記**: Ollama の画像生成は 2026-01-20 公開の experimental 機能で、公式ブログでは macOS のみ。公式 API リファレンスに画像生成のエンドポイントは未掲載。また 2026-11-07 時点で Ollama 0.17.7 の一部 MLX モデル (`gemma4:*-mlx`) が `unsupported architecture` で起動失敗することを実測） | ・`compat.detect_local_capabilities()` でローカルOllamaの対応機能を事前プローブ・ローカル画像生成非対応OSでは自動的にクラウド `ImageCapability` (OpenAI/Gemini) またはローカルDiffusers互換APIへ透過フォールバック |
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
            # v3.1: Ollama 本体と同じ解釈に揃える
            #   スキーム省略 → http / ポート省略 → 11434（スキーム明示時は http=80, https=443）
            #   0.0.0.0 (待ち受け用アドレス) は接続先として 127.0.0.1 に読み替える
            return PlatformCompat.normalize_ollama_host(env_url)
        # Dockerコンテナ内からの実行判定 (/.dockerenv の存在チェック)
        if Path("/.dockerenv").exists():
            return "http://host.docker.internal:11434"
        return "http://127.0.0.1:11434"
```

## **9.4 パッケージ構造と依存関係の分離規約 (pyproject.toml)**

マルチプラットフォームでコンパイルエラーを起こさないための標準ディレクトリ構成と `pyproject.toml` 仕様です。

v3.1 では src レイアウトを採用し、プロバイダごとに codec（純関数）を分けたサブパッケージにする（ADR-06）。今回実装する範囲（コア＋Chat の Gemini / Ollama）の詳細な構成は実装設計書 §3 を参照。

```
src/omni_ai/
├── __init__.py          # 公開API (OmniClient, BuiltinTool, 例外, 主要型)
├── compat.py            # OS差分吸収レイヤー (Windows/Linux/macOS対応)
├── config.py            # 環境変数・設定ファイルローダー (ADR-04)
├── exceptions.py        # 統一例外階層 (OmniError等)
├── types/               # Pydantic共通データモデル・マルチモーダル型（`omni_ai.types` からの import は v3.0 と互換）
├── capabilities.py      # 全10領域のSync/Async抽象基底クラス
├── pricing.py           # PricingTable (ADR-10)
├── router.py            # OmniClient, FallbackChatAdapter, TelemetryTracker
├── tools.py             # ToolRunner（@omni_tool / BuiltinTool は types に置く）
├── _http/               # transport (httpx), RetryPolicy, SSE / NDJSON パーサー
└── providers/           # プロバイダ別アダプター実装 (遅延インポート対応)
    ├── gemini/          # codec.py, files.py
    ├── ollama/          # codec.py
    ├── openai/  anthropic/  jev/  cohere/  tavily/  mistral_ocr/  e2b_sandbox/  mcp_client/   # 将来
```

```
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "omni-ai-sdk"
version = "0.1.0"   # パッケージの版は仕様書の版 (v3.1) とは独立に 0.x から始める
description = "Cross-platform Multi-Provider Unified AI SDK"
readme = "README.md"
requires-python = ">=3.11"
# コア依存は100% Pure PythonでWindows/Linux/macOSどこでもバイナリビルド不要
dependencies = [
    "pydantic>=2.7.0",
    "httpx[http2]>=0.27.0",   # h2 も Pure Python。HTTP/2 は既定では無効 (http2=True で有効化)
    "platformdirs>=4.2.0",
    "anyio>=4.4.0",
]

[project.optional-dependencies]
local = ["fastembed>=0.4"]   # v3.1: プロセス内のローカル埋め込み（ONNX）。onnxruntime はバイナリ wheel のため extras
search = ["ddgs>=9.0"]       # v3.1: キー不要の Web 検索（複数エンジンの横断検索ライブラリ）
jev = ["typesafe-sdk>=0.1.0"]
mcp = ["mcp>=1.0.0"]
sandbox = ["e2b-code-interpreter>=1.0.0"]
dev = ["pytest>=8.0.0", "pytest-asyncio>=0.23.0", "respx>=0.21.0", "hypothesis>=6.100.0", "ruff>=0.6.0", "mypy>=1.11.0"]
```

## **9.5 開発者向けコーディング規約（禁止事項チェックリスト）**

実装担当者がプルリクエストを出す前に必ず守るべき6つの鉄則です：

1. **アダプター内で sys.platform や os.name を直接参照しない**（必ず `PlatformCompat` を呼ぶ）。  
2. **open() や Path.read\_text() / write\_text() を encoding="utf-8" なしで呼ばない**（Windowsの `cp932` クラッシュ防止）。  
3. **パス文字列を "/" や "\\\\" で手動結合しない**（必ず `Path` オブジェクトで操作する）。  
4. **tempfile.NamedTemporaryFile をそのまま別関数や外部コマンドに渡さない**（必ず `PlatformCompat.safe_temp_file()` を使う）。  
5. **外部CLI（npx, uvx, ffmpeg 等）を shutil.which 解決なしで subprocess 起動しない**（Windowsでの `.cmd` 未解決エラー防止）。  
6. **localhost を文字列リテラルでハードコードしない**（IPv6解決遅延やDocker内通信を考慮し、`127.0.0.1` または `PlatformCompat.resolve_ollama_base_url()` を使う）。

# **10\. まとめ**

v3.0 / v3.1 仕様により、機能面の網羅性（全10領域のプロバイダ・ミックス）に加え、**非同期並列I/O・マルチモーダル入力・メモリ安全性・統一エラーと自動フェイルオーバー・横断コスト監視**という実運用に不可欠な要件がすべて満たされたプロダクション仕様となっています。
# **11\. 変更履歴**

## **v3.1 (2026-11-07)**

コア層＋Chat（Gemini / Ollama）の実装設計（[実装設計書](./OmniAI%20SDK%20実装設計書%20(コア＋Chat).md)）にあわせて、v3.0 の不整合・欠落を修正した。

| ID | 節 | 修正内容 | 理由 |
| :---- | :---- | :---- | :---- |
| D-01 | §2 | 表の 6〜10 行目が別の表に分かれていたのを 1 つに戻した | 書式の崩れ |
| D-02 | §3 | 例外を追加: `PermissionDeniedError` `InvalidRequestError` `UnsupportedFeatureError` `NotFoundError` `QuotaExceededError` `ProviderConnectionError` `ProviderTimeoutError` `StreamInterruptedError` `ToolLoopLimitError` `CapabilityNotConfiguredError`。`retryable` の意味を「同じ要求の再送で成功しうるか」に限定 | 接続失敗（Ollama 未起動）・残高切れ（Gemini 402）・404 などの受け皿が無かった |
| D-03 | §4 | `MediaSource` から素の `str` を除外し `UrlRef` を追加。`FileRef.provider` を追加 | `str` がパス・URL・base64 のどれか曖昧だった |
| D-04 | §4 | `@omni_tool` は呼び出し可能な `OmniTool` を返す (ADR-12) | 関数が `ToolDefinition` に置き換わり、ローカル実行できなかった |
| D-05 | §4 | `ToolCall` / `ChatMessage` に `provider_state` (ADR-11)、TOOL メッセージに `name` | Gemini 3 以降の thought signature 必須化、Gemini / Ollama が名前でツール結果を対応づけるため |
| D-06 | §4 | `StreamEvent.type` の `tool_call_delta` を `tool_call` に改名し、`reasoning_delta` `citation` と `response` を追加 | 「delta」と名乗りながら完成品を運ぶ矛盾。完了時に非ストリームと同じ `ChatResponse` を得られるように |
| D-07 | §4 | `FinishReason` を共通化し `error` を追加。`ChatResponse` に `reasoning` `citations` `to_message()`、`Usage.reasoning_tokens`、`ResponseMeta` に `time_to_first_event_ms` `attempts` `fallback_from`。`estimated_cost_usd` を Optional に | 思考トークン・出典・フォールバック経路が観測できなかった。単価不明を 0 と区別するため |
| D-08 | §5 | 共通基底 `Capability`（`provider_name`、`close`/`aclose`、コンテキストマネージャ）。主入力以降をキーワード専用に。`**kwargs` を名前空間付きの `**provider_options` に。`tool_choice` `timeout` を追加。`supported_builtins` | v3.0 §8.4 が未定義の `provider_name` を参照していた。セッションを閉じる手段が無かった |
| D-09 | §6 | `FallbackChatAdapter` に `fallback_on` `model_map`、4 メソッド全実装、ストリームの切り替え規則 (ADR-07)、再試行との順序 (ADR-08) | v3.0 は `agenerate` のみで、`retryable` とフォールバック可否が混同されていた。別プロバイダに同じモデル名を渡していた |
| D-10 | §6 | 計測を `OmniClient` の計測プロキシに一元化。`TelemetryTracker` をスレッド安全に。`__aexit__` で全 Capability を閉じる。未設定時の例外を `CapabilityNotConfiguredError` に | v3.0 では `on_response` が呼ばれる経路が無く、`__aexit__` が何もしなかった |
| D-11 | §7 | サンプルの Chat 構成を Gemini ➔ Ollama に変更し、古いモデル名を除去 | `gpt-4o` / `claude-3-5-sonnet-latest` は古い。最初に実装するのは Gemini / Ollama |
| D-12 | §8.2, §8.3 | Gemini・Ollama 列を一次資料で確認して修正（Gemini のツール JSON、併用の Preview 条件、thought signature、generateContent の legacy 扱い、Ollama の Web Search API、`:cloud` モデル、文脈の黙った切り捨て）。OpenAI・Anthropic 列は未検証と明記 | v3.0 の記述は未検証だった |
| D-13 | §8.4, §8.5 | `HybridToolManager` を「アダプターの自己申告 (ADR-09)」＋`ToolRunner` に置き換え | v3.0 のループに 7 つの欠陥（§8.4 冒頭）があった |
| D-14 | §9.1 | ADR-02 の範囲を明確化。ADR-06〜12 を追加。ADR-01 のパス表記を `compat.py` に統一 | ADR-02 と §9.4 の任意依存が矛盾して見えた |
| D-15 | §9.2, §9.3 | Ollama 画像生成の位置づけを注記。`OLLAMA_HOST` の解釈を Ollama 本体と一致させた | 一次資料・実測に基づく |
| D-16 | §9.4 | src レイアウト、`types/` パッケージ化、`_http/` `pricing.py` 追加、パッケージ版を 0.1.0 から開始、dev 依存に hypothesis | 実装設計書 §3 にあわせた |
| D-17 | §9.5 | 番号が 8〜13 になっていたのを 1〜6 に修正 | 書式の崩れ |
| D-18 | §4, §5, §8.4, §9.3 | 実装 (M0〜M6) にあわせた追補: `ChatResponse.provider_state`、`supported_builtins` をインスタンス属性に、`ToolRunResult.pending_tool_calls`、`normalize_ollama_host` を公開名に | 実装設計書 §13 |
| D-19 | §4, §5, §9.4 | Embedding を実装: `EmbeddingCapability.embed` に `task` / `normalize` / `timeout` を追加、`EmbeddingResponse.normalized` を追加。プロセス内のローカル埋め込み（fastembed、extras `[local]`、Mnemo と同じモデル）を追加 | 実装設計書 §14 |
| D-20 | §9.4 | 独自実装の provider_name をエンジン/サービス名に（`fastembed`・`ddgs`）。Search を ddgs（extras `[search]`、キー不要の横断検索）で実装 | 実装設計書 §14.4・§15 |
| D-21 | §2 | Rerank をローカル（fastembed のクロスエンコーダー、既定 bge-reranker-v2-m3-int8）で実装。スコアは 0〜1（シグモイド） | 実装設計書 §16 |
