# OmniAI SDK 実装設計書 — コア層 ＋ Chat 縦切り (Gemini / Ollama)

| 項目 | 値 |
| :--- | :--- |
| 対象仕様 | [設計仕様書 v3.1](./マルチプロバイダ統合Python%20SDK%20(OmniAI%20SDK)%20設計仕様書.md) |
| 範囲 | コア層（例外・型・Capability 抽象・設定・OS差分吸収・HTTP基盤・ルーター・テレメトリ・ツール基盤）＋ Chat Capability の Gemini / Ollama アダプター |
| 範囲外 | Chat 以外 9 領域のアダプター（抽象インタフェースのみ本書に従って整える） |
| 状態 | 初版（2026-11-07）。師匠の承認済み（2026-11-07） |
| 文書の正の向き | 実装前は**本書が正**。実装着手後に乖離が出たら、本書を実装に合わせて改訂する（M0〜M6 実装後の差分は §13） |

> 本書の外部 API に関する記述には、確認状態を付ける。
> **[確認済]** = 一次資料（公式ドキュメント）で確認、**[未検証]** = 確認できていない。URL は §12 にまとめる。

---

## 1. 目的と範囲

設計仕様書（以下「仕様書」）は 10 領域・約 25 プロバイダを定義するが、コードはまだ無い。
本書は、そのうち **コア層と Chat 1 領域を、クラウド (Google Gemini) とローカル (Ollama) の 2 プロバイダで end-to-end に通す**ための実装設計を定める。

2 プロバイダの組み合わせで、次の経路をすべて実地に通す。

| 検証したい経路 | Gemini | Ollama |
| :--- | :--- | :--- |
| Layer 1: クライアント実行ツール（関数呼び出しループ） | ✓ | ✓ |
| Layer 2: サーバー実行の組み込みツール（Web検索・コード実行） | ✓（ネイティブ） | ✗ → SDK が SearchCapability で代行 |
| フォールバック（クラウド障害 → ローカル） | 1 段目 | 2 段目 |
| ストリーミング（SSE / NDJSON という異なるワイヤ形式） | SSE | NDJSON |
| マルチモーダル入力（画像・PDF） | inline / Files API | 画像のみ base64 |

---

## 2. 設計判断一覧（実装 ADR）

仕様書 ADR-01〜05 はそのまま有効。本書で以下を追加する（仕様書 v3.1 §9.1 にも反映済み）。

| ADR | 決定テーマ | 採用方針 | 代償（トレードオフ） |
| :--- | :--- | :--- | :--- |
| **ADR-06** | Sync / Async の二重実装の作り方 | **sans-IO 方式**: 要求の組み立て・応答の解釈をすべて I/O を持たない純関数（`codec`）に寄せ、`httpx.Client` と `httpx.AsyncClient` の 2 つの薄い transport から呼ぶ。同期版を `asyncio.run()` で非同期版から作ることはしない | transport が 2 本になる（ただし各 30〜60 行程度）。代わりに、Jupyter など既にイベントループが走っている環境でも同期 API が壊れない |
| **ADR-07** | ストリーミング中のフォールバック | **最初のイベントを返す前に限り**、別プロバイダへ切り替える。1 つでもイベントを返した後の失敗は `StreamInterruptedError` として送出し、切り替えない | 途中で切れた応答は呼び出し側で再試行が要る。代わりに、2 社の出力が混ざった応答が黙って返ることは起きない |
| **ADR-08** | 再試行とフォールバックの順序 | **再試行（同一プロバイダ）→ フォールバック（次のプロバイダ）**。再試行は transport 層の `RetryPolicy`（指数バックオフ＋ジッタ、`retry_after` 優先）。フォールバックはルーター層 | 最悪の場合の待ち時間が「再試行回数 × プロバイダ数」に伸びる → `RetryPolicy.max_attempts` の既定は 2 と小さめにする |
| **ADR-09** | 組み込みツール（`BuiltinTool`）の解決 | **アダプターが自分の対応範囲を宣言する**（`supported_builtins`）。`HybridToolManager` 内の `if provider_name == ...` 分岐は廃止。未対応の組み込みツールを `agenerate(tools=[...])` に直接渡すと `UnsupportedFeatureError` | 仕様書 §8.5 にあった「`agenerate` に渡すだけで Ollama でも裏で検索が走る」は**やめる**。代行が必要なら `ai.tools.arun()`（ツールランナー）を明示的に使う。理由: `agenerate` が裏で何ターンも呼び出しを重ねると、コスト・レイテンシ・履歴の意味が呼び出しごとに変わってしまうため |
| **ADR-10** | コスト推定 | `PricingTable`（モデル ID → 入力・出力・キャッシュ単価）を**注入する方式**。単価が分からないモデルの `estimated_cost_usd` は **`None`**（0 にしない）。Ollama のローカルモデルは 0.0、Ollama 経由のクラウドモデル（`:cloud` / `-cloud`）は `None`（§8.1） | 単価表の保守は利用者の責任になる。SDK に同梱するのは「既定値（取得日付き）」の扱い |
| **ADR-11** | プロバイダ固有の不透明な状態 | `ChatMessage` と `ToolCall` に `provider_state: dict` を持たせ、アダプターだけが読み書きする（例: Gemini の thought signature を次のターンへ引き継ぐ） | 履歴をプロバイダ間で使い回すと、他社の `provider_state` は捨てられる（フォールバック先では無視する） |
| **ADR-12** | `@omni_tool` の戻り値 | 関数を `OmniTool`（`ToolDefinition` を継承し、元の関数を `handler` として保持する**呼び出し可能な**オブジェクト）に置き換える | 「関数そのもの」ではなくなる（`inspect` などで見ると型が変わる） |

---

## 3. パッケージ構成（今回作るもの）

```text
src/omni_ai/
├── __init__.py            # 公開 API の再エクスポート（遅延 import なし・軽量なもののみ）
├── _version.py
├── exceptions.py          # 統一例外階層
├── types/
│   ├── __init__.py        # 公開型の再エクスポート（仕様書の omni_ai.types を維持）
│   ├── common.py          # Usage, ResponseMeta, FinishReason
│   ├── media.py           # MediaSource, FileRef, UrlRef, MediaPart, TextPart
│   ├── chat.py            # Role, ChatMessage, ChatResponse, StreamEvent, Citation
│   ├── tools.py           # ToolDefinition, OmniTool, ToolCall, BuiltinTool, omni_tool
│   └── other.py           # Judge / Image / Audio / ... の応答型（仕様書 §4 をそのまま移す）
├── capabilities.py        # 全 10 領域の Sync/Async 抽象基底（Chat 以外は形だけ）
├── config.py              # 設定・認証の 4 段階解決 (ADR-04)
├── compat.py              # OS差分吸収（仕様書 §9.3）＋ detect_local_capabilities
├── _http/
│   ├── __init__.py
│   ├── transport.py       # SyncTransport / AsyncTransport（httpx 薄ラッパー）
│   ├── retry.py           # RetryPolicy
│   ├── sse.py             # SSE パーサー（行単位・sans-IO）
│   └── ndjson.py          # NDJSON パーサー（sans-IO）
├── _media.py              # MediaSource → bytes / base64 / stream の解決（メモリ上限つき）
├── pricing.py             # PricingTable
├── router.py              # FallbackChatAdapter, TelemetryTracker, OmniClient, 計測プロキシ
├── tools.py               # ToolRunner（クライアント側ループ）, BuiltinTool 代行
└── providers/
    ├── __init__.py        # 遅延 import（__getattr__）
    ├── _base.py           # BaseAdapter（provider_name, transport 管理, close/aclose）
    ├── gemini/
    │   ├── __init__.py    # GeminiChatAdapter
    │   ├── codec.py       # 共通型 ⇔ Gemini JSON（sans-IO）
    │   └── files.py       # Files API アップロード
    └── ollama/
        ├── __init__.py    # OllamaChatAdapter
        └── codec.py       # 共通型 ⇔ Ollama JSON（sans-IO）
tests/
├── unit/                  # codec・パーサー・例外変換・ルーター（ネットワーク不要）
├── contract/              # 全 Chat アダプター共通の契約テスト（respx で応答を再生）
├── fixtures/              # 記録済み応答（gemini/*.json, *.sse / ollama/*.ndjson）
└── live/                  # 実 API（環境変数があるときだけ実行）
```

- **src レイアウト**を採用（テストが、インストールされたパッケージを確実に import するように）。
- `types` はモジュールからパッケージに変える。ただし `from omni_ai.types import ChatMessage` は仕様書どおりに動かす。
- プロバイダごとにサブパッケージにして、`codec.py`（純関数）と transport を呼ぶ側を分ける（ADR-06）。

### 3.1 依存関係

| 区分 | パッケージ | 備考 |
| :--- | :--- | :--- |
| コア（仕様書 ADR-03 どおり 4 つ） | `pydantic>=2.7`, `httpx>=0.27`, `platformdirs>=4.2`, `anyio>=4.4` | `httpx[http2]` の `h2` も Pure Python。HTTP/2 は**既定では無効**（`http2=True` で有効化）|
| dev | `pytest`, `pytest-asyncio`（または anyio プラグイン）, `respx`, `ruff`, `mypy`, `hypothesis` | `hypothesis` はストリームパーサーの性質テスト用に追加 |

Gemini・Ollama のどちらも公式 SDK には依存しない（仕様書 ADR-02）。

---

## 4. レイヤー構造

```text
 利用者コード
     │  ai.chat.agenerate(...) / ai.tools.arun(...)
 ┌───▼────────────────────────────────────────────┐
 │ OmniClient（router.py）                         │
 │   計測プロキシ: latency・コストを記録し on_response を呼ぶ │
 ├────────────────────────────────────────────────┤
 │ FallbackChatAdapter（任意）: 例外の種類でプロバイダを順に切り替え │
 ├────────────────────────────────────────────────┤
 │ ChatCapability 実装（GeminiChatAdapter 等）         │
 │   ├ codec（sans-IO）: 共通型 ⇔ ワイヤ JSON、例外変換  │
 │   └ transport: httpx ＋ RetryPolicy               │
 └────────────────────────────────────────────────┘
```

- **依存の向き**: `providers → (types, exceptions, _http, compat, config)`。`router` は `capabilities` だけを知り、個別アダプターを import しない。
- **計測は OmniClient 側で一元化する**。アダプターは `ResponseMeta` に `provider / model / usage / request_id` を詰めるだけで、時間計測・コスト計算・フックの呼び出しはしない。これで仕様書 v3.0 の `on_response` が呼ばれない問題（仕様書 §11 D-10）を解消する。

---

## 5. モジュール別設計

### 5.1 `exceptions.py`

仕様書 §3 の 5 クラスに、ネットワーク系・要求不正系・ストリーム系を足す。

```text
OmniError(message, provider, model=None, status_code=None, retryable=False, raw_error=None, request_id=None)
├── AuthenticationError            401/403, キー不正           retryable=False
├── PermissionDeniedError          403（権限・地域制限）       retryable=False  ※AuthenticationError のサブクラス
├── InvalidRequestError            400（その他の要求不正）     retryable=False
│   ├── ContextLengthExceededError 400（文脈長超過）           retryable=False
│   └── UnsupportedFeatureError    SDK が事前に検出（例: 組み込みツール未対応・画像非対応モデル）
├── NotFoundError                  404（モデル未存在など）     retryable=False
├── QuotaExceededError             402（Gemini 前払い残高ゼロ等） retryable=False ※フォールバック対象
├── RateLimitError                 429                         retryable=True, retry_after_seconds
├── ProviderOverloadedError        500/502/503/504/529         retryable=True
├── ProviderConnectionError        接続拒否・DNS 失敗など      retryable=True   ※Ollama 未起動の典型
│   └── ProviderTimeoutError       タイムアウト                retryable=True
├── StreamInterruptedError         ストリーム途中の切断        retryable=False（ADR-07）
├── SchemaValidationError          構造化出力の検証失敗        retryable=False
├── ToolLoopLimitError             ToolRunner が max_turns 到達（partial_result 付き）
└── CapabilityNotConfiguredError   OmniClient の未設定 Capability へのアクセス
```

- `retryable` は「**同じ要求をそのまま送り直せば成功しうるか**」だけを表す。フォールバックするかどうかは `FallbackChatAdapter.fallback_on`（既定: `RateLimitError, ProviderOverloadedError, ProviderConnectionError, QuotaExceededError`）で別に決める。`ContextLengthExceededError` を `fallback_on` に足せば「文脈の長いプロバイダへ逃がす」こともできる。
- 例外変換はアダプターの codec に置く純関数 `map_error(status, body, headers) -> OmniError` とし、記録済みの応答でテストする。
- `raw_error` には httpx の例外か、`httpx.Response` から作った軽量な記録（ステータス・本文の先頭 2KB・ヘッダの一部）を入れる。**API キーを含むヘッダは記録しない**。

### 5.2 `types/`（共通データモデル）

仕様書 §4 からの変更点だけを記す。

```python
FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "error", "unknown"]

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0          # 追加: Gemini thoughtsTokenCount 等
    total_tokens: int = 0

class ResponseMeta(BaseModel):
    provider: str
    model: str
    latency_ms: float = 0.0
    time_to_first_event_ms: float | None = None   # 追加: ストリーミング時
    estimated_cost_usd: float | None = None       # 変更: 不明なら None (ADR-10)
    request_id: str | None = None
    usage: Usage = Field(default_factory=Usage)
    attempts: int = 1                              # 追加: 再試行を含めた試行回数
    fallback_from: list[str] = Field(default_factory=list)  # 追加: 失敗して飛ばしたプロバイダ

class UrlRef(BaseModel):            # 追加: str の曖昧さを解消
    url: str

MediaSource = bytes | Path | BinaryIO | FileRef | UrlRef   # 変更: 素の str を除外

class MediaPart(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)   # BinaryIO を許すため
    type: Literal["image", "audio", "video", "document"]
    source: MediaSource
    mime_type: str

class ToolCall(BaseModel):
    id: str                          # プロバイダが id を返さない場合は SDK が採番 ("call_<uuid4 hex 12桁>")
    name: str
    arguments: dict[str, Any]
    provider_state: dict[str, Any] = Field(default_factory=dict)   # ADR-11

class ChatMessage(BaseModel):
    role: Role
    content: str | list[ContentPart]
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None          # 追加: TOOL メッセージのツール名（Gemini/Ollama は名前で結果を返す）
    provider_state: dict[str, Any] = Field(default_factory=dict)   # ADR-11

class Citation(BaseModel):          # 追加: 組み込み Web 検索の出典
    title: str | None = None
    url: str
    start_index: int | None = None
    end_index: int | None = None

class ChatResponse(BaseModel):
    content: str | None = None
    parsed: Any | None = None
    reasoning: str | None = None     # 追加: 思考の要約（取得できるプロバイダのみ）
    tool_calls: list[ToolCall] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)   # 追加
    finish_reason: FinishReason = "stop"
    meta: ResponseMeta

    def to_message(self) -> ChatMessage: ...   # 追加: 履歴にそのまま積めるアシスタントメッセージを作る

class StreamEvent(BaseModel):
    type: Literal["text_delta", "reasoning_delta", "tool_call", "citation", "message_complete"]
    delta_text: str = ""
    tool_call: ToolCall | None = None          # "tool_call" は常に完成した ToolCall（アダプターがバッファする）
    citation: Citation | None = None
    finish_reason: FinishReason | None = None
    response: ChatResponse | None = None       # message_complete のとき、全体を組み立てた ChatResponse
    meta: ResponseMeta | None = None
```

- `StreamEvent.type` の `"tool_call_delta"` は `"tool_call"` に改める。v3.0 では「delta」と名乗りながら完成した `ToolCall` を運ぶ定義になっており、意味が矛盾していたため。引数 JSON の断片はアダプターの内部で組み立て、利用者には完成品だけを見せる。
- `message_complete` で `response` を返すので、ストリームを最後まで読んだ利用者は、非ストリームと同じ `ChatResponse` を得られる。
- 型注釈は Python 3.11 の組み込みジェネリクス（`list[...]`、`X | None`）に統一する。

### 5.3 `types/tools.py` — `omni_tool` / `OmniTool` / `BuiltinTool`

```python
class OmniTool(ToolDefinition):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    handler: Callable[..., Any] = Field(exclude=True)
    is_async: bool = False

    def __call__(self, *args, **kwargs): return self.handler(*args, **kwargs)
    async def ainvoke(self, arguments: dict[str, Any]) -> Any: ...   # sync なら anyio.to_thread で実行

def omni_tool(func=None, *, name: str | None = None, description: str | None = None, strict: bool = True): ...
```

- `@omni_tool` と `@omni_tool(name=...)` の両方の書き方を受け付ける。
- 引数モデルは `pydantic.create_model` で作り、**`model_json_schema()` の出力から `title` を取り除き、`$defs` を展開する**。Gemini の関数宣言が受け付けるスキーマの範囲が狭いため、ここで正規化する（§7.3）。
- `self` / `cls` / `*args` / `**kwargs` を含む関数は、デコレート時に `TypeError` にする。
- Docstring の Google 形式 `Args:` 節から、引数の説明をスキーマの `description` に写す（簡易パーサー。失敗しても説明が無いだけで済ませる）。
- `BuiltinTool`（`WEB_SEARCH`, `CODE_EXECUTION`）は仕様書のとおり `str` 列挙型にする。
- `ToolSpec = ToolDefinition | OmniTool | BuiltinTool` を、`tools` 引数の要素の型とする。

### 5.4 `capabilities.py`

```python
class Capability(ABC):
    provider_name: ClassVar[str]                 # 追加（v3.0 §8.4 が参照していたのに未定義だった）
    def close(self) -> None: ...                 # 追加: 同期 transport を閉じる
    async def aclose(self) -> None: ...          # 追加: 非同期 transport を閉じる
    def __enter__/__exit__/__aenter__/__aexit__  # 追加

class ChatCapability(Capability):
    supported_builtins: ClassVar[frozenset[BuiltinTool]] = frozenset()   # ADR-09

    def generate(self, messages, *, model=None, temperature=None, max_tokens=None,
                 tools: Sequence[ToolSpec] | None = None,
                 tool_choice: Literal["auto", "none", "required"] | str = "auto",   # 追加
                 response_schema: type[BaseModel] | None = None,
                 timeout: float | None = None,                                       # 追加
                 **provider_options) -> ChatResponse
    async def agenerate(...) -> ChatResponse
    def stream(...) -> Iterator[StreamEvent]
    def astream(...) -> AsyncIterator[StreamEvent]
```

- `model` 以降の引数は**キーワード専用**にする（`*,`）。位置引数で渡すとフォールバック先で意味がずれる事故を防ぐ。
- `**kwargs` は `**provider_options` と改名する。アダプターは**知らないキーを黙って捨てない**: 自分のキーでなければ無視したうえで `logging` の debug に出す（縮退経路のログ）。プロバイダ固有の指定は、`gemini={...}`・`ollama={...}` のように名前空間を付けて渡す方式にする（フォールバック先のプロバイダに、他社向けの指定が混ざらないように）。
- Chat 以外の 9 領域にも `Capability` 基底と `provider_name` を付ける。シグネチャはそれ以外、仕様書 §5 のまま。

### 5.5 `config.py`（ADR-04 の具体化）

解決順: ①引数 → ②`OMNI_<PROVIDER>_<KEY>` → ③各社の標準環境変数 → ④設定ファイル。

| 設定 | ① 引数 | ② OMNI_* | ③ 各社標準 | ④ 設定ファイル |
| :--- | :--- | :--- | :--- | :--- |
| Gemini API キー | `api_key=` | `OMNI_GEMINI_API_KEY` | `GEMINI_API_KEY`, `GOOGLE_API_KEY`（この順） | `[gemini] api_key` |
| Gemini base URL | `base_url=` | `OMNI_GEMINI_BASE_URL` | — | `[gemini] base_url` |
| Ollama base URL | `base_url=` | `OMNI_OLLAMA_BASE_URL` | `OLLAMA_HOST` | `[ollama] base_url` → 最後に `compat.resolve_ollama_base_url()` |

- 設定ファイルは `PlatformCompat.get_config_dir() / "config.toml"` に置き、標準ライブラリの `tomllib` で読む（依存を増やさない）。読むときに `encoding` 問題は起きない（`tomllib` はバイナリで読む）。
- **`.env` は SDK からは読まない**。読むのはアプリ側の責任とする（ADR-04 の理由欄で `.env` に触れているが、自動読み込みは副作用が大きいため）。
- 解決結果には「どの段で決まったか」を持たせ、`OmniClient.describe_config()` でキー本体を伏せて表示できるようにする（問い合わせ対応用）。

### 5.6 `compat.py`

仕様書 §9.3 の `PlatformCompat` をそのまま実装し、以下を足す。

- `resolve_ollama_base_url()`: `OLLAMA_HOST` の値は `host:port` 形式と、スキームを含む形式の両方があり得るので、両方を正規化する。`0.0.0.0` は接続先としては使えないので `127.0.0.1` に置き換える。
- `is_in_container()`: `/.dockerenv` に加えて、`/proc/1/cgroup` の確認も Linux に限って行う（`compat` の中だけで OS を分岐する）。
- コンテナの中で `127.0.0.1:11434` への接続が拒否されたら、`host.docker.internal` を **1 回だけ**試す。この判断は `OllamaChatAdapter` の初回接続時に行い、結果はインスタンスに保持する。
- `detect_local_capabilities()` は、今回は Ollama の `/api/tags` と `/api/show` の結果をまとめるところまでを作る（§7.4）。画像生成の判定は今回の範囲外。

### 5.7 `_http/` — transport・再試行・ストリームパーサー

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2               # 初回を含む
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25                # ±25%
    respect_retry_after: bool = True    # RateLimitError.retry_after_seconds を優先（ただし max_delay を超えたら再試行しない）
```

- 再試行の対象は `retryable=True` の例外だけ。**ストリームは、最初のバイトを受け取るまで**しか再試行しない。
- 待機は `time.sleep`（同期）と `anyio.sleep`（非同期）を使い分ける。どちらの経路も、`RetryPolicy.next_delay()` という同じ純関数で待ち時間を決める。
- タイムアウトの既定値は `httpx.Timeout(connect=10, read=120, write=60, pool=10)`。Ollama は初回にモデルをロードするため、`read` の既定を 300 秒にする。
- `SSEParser` / `NDJSONParser` は、バイト列を受け取ってイベントを返すだけの状態機械にする（I/O を持たない）。行が途中で分割されるケース、CRLF、マルチバイト文字の分断は `hypothesis` で性質テストを行う。
- transport は、`User-Agent: omni-ai-sdk/<version> (<provider>)` を付ける。

### 5.8 `_media.py` — MediaSource の解決

| 入力 | 扱い |
| :--- | :--- |
| `bytes` | そのまま |
| `Path` | サイズを先に `stat()` で確認し、インライン上限を超えていれば、Files API を持つプロバイダならアップロード経路へ回す。無ければ `UnsupportedFeatureError` |
| `BinaryIO` | `seek`/`tell` が使えればサイズを測ってから扱う。使えなければチャンクで読み、上限を超えた時点で打ち切る |
| `FileRef` | そのプロバイダの参照ならそのまま渡す。他社の参照なら `UnsupportedFeatureError` |
| `UrlRef` | プロバイダが URL 入力を受け付けるならそのまま渡す。受け付けなければ、SDK は**勝手にダウンロードしない**で `UnsupportedFeatureError` にする（SSRF とメモリの両面のリスクを避けるため） |

- base64 にするとメモリ上のサイズは約 1.33 倍になる。インライン上限は、プロバイダの上限ではなく、base64 にした後のサイズで判定する。

### 5.9 `router.py`

#### 計測プロキシ
`OmniClient.chat` は、設定されたアダプターをそのまま返さず、`_InstrumentedChat(inner, tracker, pricing, on_response)` で包んで返す。

1. `time.perf_counter()` で計測を始める
2. 内側のアダプターを呼ぶ
3. `meta.latency_ms` を詰め、`pricing.estimate(meta)` で `estimated_cost_usd` を計算する
4. `tracker.record(meta)` を呼ぶ → `on_response(meta)` を呼ぶ（フックが例外を出しても**握りつぶして** `logging.warning` に出す。本処理を失敗させない）
5. ストリームの場合は、最初のイベントで `time_to_first_event_ms` を記録し、`message_complete` のときに 3〜4 を行う

`TelemetryTracker` は、非同期処理が並列に記録しても壊れないように、`threading.Lock` で守る。集計は `total_cost_usd`（`None` は合計に含めず、`unpriced_calls` として件数を別に数える）、`total_latency_ms`、`by_provider()` を持つ。

#### FallbackChatAdapter
```python
class FallbackChatAdapter(ChatCapability):
    provider_name = "fallback"
    def __init__(self, providers: Sequence[ChatCapability], *,
                 fallback_on: tuple[type[OmniError], ...] = (RateLimitError, ProviderOverloadedError, ProviderConnectionError, QuotaExceededError),
                 model_map: Mapping[str, Mapping[str, str]] | None = None): ...
```
- `generate` / `agenerate` / `stream` / `astream` の **4 つすべて**を実装する（v3.0 は `agenerate` だけだった）。
- **`model` の扱い**: 呼び出し側が渡した `model` が `"gemini-2.5-flash"` のような特定プロバイダの ID のとき、そのまま Ollama に渡すと必ず失敗する。そこで、`model` が渡されたら `model_map[provider_name][model]` で置き換え、対応が無ければ各アダプターの `default_model` を使う。
- 組み込みツールを含む要求で、そのツールを持たないアダプターは**飛ばす**（`UnsupportedFeatureError` を `fallback_from` に記録する）。
- 全滅したときは、最後の例外を送出し、それまでの例外を `__notes__`（`add_note`）に要約して付ける。
- ストリームは ADR-07 に従う。

#### OmniClient
- コンストラクタは仕様書のとおり。加えて `pricing: PricingTable | None` と `retry: RetryPolicy | None` を受け取る。
- `__aexit__` / `aclose()` で、設定済みの全 Capability の `aclose()` を呼ぶ（v3.0 は何もしない `pass` だった）。同期版の `close()`、`__enter__` / `__exit__` も用意する。
- 未設定の Capability にアクセスしたときの例外は、仕様書の `NotImplementedError` から **`CapabilityNotConfiguredError(OmniError)`** に変える（`NotImplementedError` は「抽象メソッドの未実装」を表す慣用があり、意味が紛らわしいため）。

### 5.10 `tools.py` — ToolRunner（クライアント側のツールループ）

仕様書 §8.4 の `HybridToolManager.execute_agent_loop` を置き換える。

```python
class ToolRunner:
    def __init__(self, chat: ChatCapability, *, search: SearchCapability | None = None,
                 sandbox: SandboxCapability | None = None, max_turns: int = 8,
                 parallel_tool_calls: bool = True,
                 on_tool_error: Literal["return_to_model", "raise"] = "return_to_model"): ...
    def run(self, messages, tools: Sequence[ToolSpec], **opts) -> ToolRunResult
    async def arun(self, messages, tools, **opts) -> ToolRunResult

class ToolRunResult(BaseModel):
    response: ChatResponse          # 最後の応答
    messages: list[ChatMessage]     # 追加されたものを含む全履歴
    turns: int
    metas: list[ResponseMeta]       # 各ターンの meta（合計コストは OmniClient 側でも集計される）
```

ループの手順（v3.0 で不足していた部分を **太字** で示す）:

1. `BuiltinTool` を解決する。アダプターの `supported_builtins` に含まれていればそのまま渡す。含まれていなければ代行用の `OmniTool` に置き換える（`WEB_SEARCH` → `search` Capability、`CODE_EXECUTION` → `sandbox` Capability）。代行に必要な Capability が無ければ `UnsupportedFeatureError`。
2. `agenerate` を呼ぶ。
3. `tool_calls` が空なら終了する。
4. **アシスタントの応答 (`response.to_message()`) を、`tool_calls` と `provider_state` も含めて履歴に足す。**
5. 各 `ToolCall` に対応する handler を探す。**見つからない名前は「unknown tool」という結果をモデルに返す**（黙って無視しない）。
6. **handler を `ainvoke` で実行する**（同期関数はスレッドで実行する。`parallel_tool_calls=True` なら `anyio` のタスクグループで並行して実行する）。
7. **引数が検証に失敗したときや、handler が例外を出したときは、`on_tool_error` に従う。**
8. `Role.TOOL`・`tool_call_id`・`name` を付けて結果を履歴に足し、2. に戻る。
9. **`max_turns` に達したら、`ToolLoopLimitError(OmniError)` を送出する**（途中の `ToolRunResult` は例外の属性に持たせる）。

OmniClient からは `ai.tools.arun(...)` として使う。`ai.tools` は、設定済みの `chat` / `search` / `sandbox` から `ToolRunner` を組み立てる。

### 5.11 `pricing.py`

```python
class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None
    source: str          # 単価の出典 URL
    as_of: date          # 取得日

class PricingTable:
    def __init__(self, prices: Mapping[tuple[str, str], ModelPrice] | None = None, *, is_free=lambda provider, model: provider == "ollama" and not model.endswith((":cloud", "-cloud"))): ...
    def estimate(self, meta: ResponseMeta) -> float | None: ...
```

- 単価表を同梱するかどうかは、**今回は同梱しない**（空の表 ＋ Ollama は 0 円）。同梱すると古くなった単価が黙って使われ続けるため。必要になったら、`as_of` 付きで追加する。

---

## 6. Chat アダプター共通の設計

### 6.1 アダプターの骨格

```python
class GeminiChatAdapter(BaseAdapter, ChatCapability):
    provider_name = "gemini"
    supported_builtins = frozenset({BuiltinTool.WEB_SEARCH, BuiltinTool.CODE_EXECUTION})

    def __init__(self, *, api_key=None, base_url=None, default_model="<§7.1>",
                 retry: RetryPolicy | None = None, timeout=None,
                 http_client: httpx.Client | None = None,
                 async_http_client: httpx.AsyncClient | None = None): ...
```

- `http_client` / `async_http_client` を外から注入できるようにする（テストと、プロキシ設定などの利用者独自の設定のため）。注入されたクライアントは、アダプター側では**閉じない**。
- transport は、最初に使ったときに作る（同期だけ使う利用者のために `AsyncClient` を作らない）。

### 6.2 1 回の呼び出しの流れ（両アダプター共通）

```text
messages, opts
  → codec.build_request(messages, opts)          … 純関数：JSON と URL を作る、検証違反は UnsupportedFeatureError
  → transport.post / stream（RetryPolicy つき）
  → 非 2xx: codec.map_error(status, body, headers) → OmniError
  → codec.parse_response(json) / codec.StreamDecoder.feed(chunk)
  → ChatResponse / StreamEvent
  → response_schema があれば pydantic で検証 → parsed（失敗時 SchemaValidationError）
```

### 6.3 契約テスト（両アダプターに同じテストを当てる）

`tests/contract/test_chat_contract.py` を、`[gemini, ollama]` でパラメータ化して実行する。記録済みの応答を respx で再生し、次を確かめる。

| # | 契約 |
| :--- | :--- |
| C-01 | テキストだけの応答で `content` と `finish_reason="stop"` と `usage` が埋まる |
| C-02 | ツール呼び出しの応答で `tool_calls` に id / name / arguments(dict) が入り、`finish_reason="tool_calls"` |
| C-03 | `to_message()` → ツール結果 → 再送信で、ワイヤ上の履歴がプロバイダ仕様どおりになる（記録したリクエストと突き合わせる） |
| C-04 | ストリームをすべて連結した結果が、非ストリームの `ChatResponse` と等しくなる（同じ録画から作る） |
| C-05 | `message_complete` はちょうど 1 回、最後に来る |
| C-06 | 429 / 503 / 400（文脈長）/ 401 / 404 / 接続拒否 が、それぞれ決められた例外に変換される |
| C-07 | 再試行: 429 のあと 200 なら、`attempts=2` で成功する |
| C-08 | `response_schema` を指定すると `parsed` が型どおりに得られ、壊れた JSON なら `SchemaValidationError` になる |
| C-09 | `close()` / `aclose()` のあとに呼ぶと、明確な例外になる |

---

## 7. GeminiChatAdapter

### 7.1 方針

| 項目 | 決定 | 根拠・代償 |
| :--- | :--- | :--- |
| API | **`generateContent` / `streamGenerateContent`（v1beta）** を使う | [確認済] 公式索引は Interactions API を既定とし、generateContent を「legacy」と呼ぶ。ただし移行ガイドは generateContent を「fully supported」と書いており、サンセット日は見つからなかった [未検証]。関数呼び出し・組み込みツール・ストリームの資料がそろっているのは generateContent の側なので、こちらで縦切りを通す。**代償**: 将来、移行が要る。codec を差し替えられる構造（§6.2）にして、`InteractionsCodec` を後から足せるようにしておく |
| 版 | `v1beta`（`base_url` で変更可） | [確認済] Context Caching と Files 関連が v1beta |
| 認証 | `x-goog-api-key` ヘッダ。`?key=` は使わない | [確認済] ヘッダが正式。キーが URL に入るとログに残りやすいため |
| 既定モデル | **`gemini-3.1-flash-lite`**（師匠の決定 2026-11-07） | 当初は公式コード例の既定に合わせて `gemini-3.8-flash` にしたが、実測で同じキーでは無応答のままタイムアウトした。`gemini-3.1-flash-lite` は実測で動いた |
| 注意 | `gemini-2.5-flash` / `-pro` は deprecated。2026-12-01 から別モデルへ自動ルーティングされる | [確認済] 仕様書 v3.0 の例のモデル名は古い |

### 7.2 要求の写像（共通型 → Gemini）

| 共通型 | Gemini | 備考 |
| :--- | :--- | :--- |
| `Role.SYSTEM` | `systemInstruction.parts[].text` | [確認済] text のみ。メディアを含めば `UnsupportedFeatureError`。SYSTEM が複数あれば改行で連結する |
| `Role.USER` | `contents[{role:"user"}]` | |
| `Role.ASSISTANT` | `contents[{role:"model"}]` | `provider_state["gemini"]["parts"]` があれば、**その parts をそのまま**送る（§7.4） |
| `Role.TOOL`（連続する分をまとめる） | **1 つの** `{role:"user", parts:[{functionResponse:{name, id, response}}...]}` | [確認済] 並列呼び出しの結果は、FC1, FC2, FR1, FR2 の順に並べる必要がある（交互に並べると 400）。`response` は object が必須なので、`str` の結果は `{"result": ...}`、例外は `{"error": ...}` で包む |
| 最後のメッセージが ASSISTANT | **送信前には拒否しない**（API の 400 を例外に写像する） | 資料では Gemini 3.6 以降が 400。**実測で `gemini-3.1-flash-lite` は 200 で続きを返した**ため、可否をモデルに任せる |
| `TextPart` | `{text}` | |
| `MediaPart` + bytes / Path / BinaryIO | インライン上限以下なら `inlineData{mimeType, data(base64)}`、超えたら Files API でアップロードして `fileData` | [確認済] インラインは要求全体で 100MB（PDF は 50MB）まで。SDK のインライン上限の既定は **20MB**（メモリを守るための保守的な値。変更可） |
| `MediaPart` + `FileRef(provider="gemini")` | `fileData{fileUri, mimeType}` | |
| `MediaPart` + `UrlRef` | `fileData{fileUri: url, mimeType}` | [確認済] 外部 URL は 1 ファイル 15MB まで |
| `temperature` | `generationConfig.temperature` | [確認済] Gemini 3.6 Flash 以降は**黙って無視される**（将来は 400 になる予告）。SDK は値を送り、debug ログに「無視される可能性あり」と出す |
| `max_tokens` | `generationConfig.maxOutputTokens` | |
| `response_schema` | `responseMimeType:"application/json"` ＋ `responseJsonSchema` | [確認済] 現行の参照ページに `responseSchema` は無い。ガイドの例は `responseFormat.text` を使っているが、`responseJsonSchema` が非推奨になったという記述は見つからなかった [未検証]。スキーマは §5.3 と同じ正規化を通す |
| `tools`（関数） | `tools:[{functionDeclarations:[{name, description, parametersJsonSchema}]}]` | [確認済] `parameters`（OpenAPI サブセット）ではなく `parametersJsonSchema` を使う |
| `BuiltinTool.WEB_SEARCH` | `{"google_search": {}}` | [確認済] v3.0 §8.4 の `{"type": "google_search"}` は**誤り** |
| `BuiltinTool.CODE_EXECUTION` | `{"code_execution": {}}` | [確認済] |
| 組み込みツールと関数の併用 | `toolConfig.includeServerSideToolInvocations: true` を付け、mode は `VALIDATED` | [確認済] Gemini 3 / 4 の **Preview 機能**。2.5 系では使えるか不明 [未検証] |
| `tool_choice` | `auto`→`AUTO`、`none`→`NONE`、`required`→`ANY`、ツール名→`ANY` ＋ `allowedFunctionNames` | [確認済] |
| 思考の指定 | `gemini={"thinking_level": "low", "include_thoughts": True}` → `thinkingConfig` | [確認済] Gemini 3 以降は `thinkingLevel`、2.5 は `thinkingBudget`。3.8 Flash は `minimal` を指定するとエラー |

### 7.3 応答の写像（Gemini → 共通型）

| Gemini | 共通型 | 備考 |
| :--- | :--- | :--- |
| `candidates[0].content.parts[].text`（`thought` ではないもの） | `content`（連結） | parts を**位置に依存せず**すべて走査する（[確認済] 併用時、functionCall が末尾に来るとは限らない） |
| `parts[].text` で `thought: true` | `reasoning` | `include_thoughts` を指定したときだけ現れる |
| `parts[].functionCall{name, id, args}` | `ToolCall(id, name, arguments)` | [確認済] Gemini 3 は必ず一意な id を返す。id が無ければ SDK が採番する |
| `content`（parts 全体） | `ChatResponse.to_message().provider_state["gemini"]["parts"]` | thought signature などを**欠かさず保持する**ため（§7.4） |
| `executableCode` / `codeExecutionResult` | `provider_state` にだけ保持する | 共通型にサーバー側ツールの結果を出す項目は、今回は作らない（§11 残留リスク R-05） |
| `groundingMetadata.groundingSupports[].segment` ＋ `groundingChunks[].web` | `citations[]` | [確認済] フィールド名 |
| `finishReason` | 下表 | |
| `usageMetadata` | `Usage` | `promptTokenCount`→prompt（キャッシュ分を含む）、`candidatesTokenCount`→completion、`cachedContentTokenCount`→cached、`thoughtsTokenCount`→reasoning、`totalTokenCount`→total [確認済] |
| `responseId` | `meta.request_id` | [確認済] |
| `modelVersion` | `meta.model` | 要求したモデル名ではなく、**実際に応答したモデル**を記録する（2026-12-01 の自動ルーティングを検出できるように） |
| `promptFeedback.blockReason`（candidates が空） | `content=None`, `finish_reason="content_filter"` | [確認済] |

`finishReason` の写像（[確認済] 列挙値）:

| Gemini | 共通 |
| :--- | :--- |
| `STOP` | `stop`（functionCall を含んでいれば `tool_calls`） |
| `MAX_TOKENS` | `length` |
| `SAFETY`, `RECITATION`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `IMAGE_SAFETY`, `IMAGE_PROHIBITED_CONTENT`, `IMAGE_RECITATION` | `content_filter` |
| `MALFORMED_FUNCTION_CALL`, `UNEXPECTED_TOOL_CALL`, `TOO_MANY_TOOL_CALLS`, `MISSING_THOUGHT_SIGNATURE`, `MALFORMED_RESPONSE` | `error` |
| `CONTINUATION` | `length`（`continuationToken` は `provider_state` に保持する。継続取得は今回の範囲外） |
| その他（`LANGUAGE`, `OTHER`, `ESCALATION`, …、未知の値） | `unknown`（元の値を debug ログに出す） |

### 7.4 thought signature の扱い（最重要）

[確認済] Gemini 3 / 4 の関数呼び出しでは、モデルが返した `thoughtSignature` を次のターンで**元の part にそのまま付けて**返さないと、400（`missing a thought_signature`）になる。並列呼び出しでは、最初の functionCall にだけ付く。

- アダプターは、モデル応答の `content.parts` を**加工せずに**、`provider_state["gemini"]["parts"]` に丸ごと保存する。
- 再送信のときに `provider_state["gemini"]` があれば、`ChatMessage.content` や `tool_calls` から parts を組み立て直さずに、保存した parts をそのまま使う。
- **他社のアシスタントメッセージ**（例: Ollama にフォールバックしたあとの履歴）を Gemini に送るときは、functionCall の part に、公式に用意されているダミー署名 `"skip_thought_signature_validator"` を付ける [確認済]。
- 利用者がアシスタントメッセージを自分で組み立てた場合（`provider_state` が無い場合）も、同じダミー署名を付ける。

### 7.5 ストリーミング

- `POST .../{model}:streamGenerateContent?alt=sse`。各イベントは `GenerateContentResponse` と同じ形 [確認済]。
- `data:` 行ごとに JSON として読み、text の差分 → `text_delta`、thought → `reasoning_delta`、functionCall → `tool_call`（Gemini は functionCall を完成した形で返すので、そのまま流す）。
- `finishReason` が現れたチャンクを最後のチャンクとみなす。`usageMetadata` は、**最後に受け取った値**を採用する（[未実測] 途中のチャンクにも usage が載る例がある）。
- 署名が空の text part に付いて届くことがある [確認済] ので、ストリームでも parts を蓄積して `provider_state` を組み立てる。
- SSE の生のフレーム形式と、ストリーム途中のエラーの形は **[未実測]**。`data:` 行の JSON に `error` キーがあれば、§7.6 の写像で例外にする（最初のイベントの前なら通常の例外、後なら `StreamInterruptedError`）。

### 7.6 エラーの写像

[確認済] 本文の形は `{"error": {"code", "message", "status", "details": [...]}}`。

| HTTP | `status` / 条件 | 例外 |
| :--- | :--- | :--- |
| 400 | `details[].reason == "API_KEY_INVALID"` | `AuthenticationError`（[確認済] キー不正は 401 ではなく 400） |
| 400 | message が文脈長の超過を示す | `ContextLengthExceededError`（**[未検証]** 文脈長超過のステータスと文言は資料に無い。判定は message の部分一致に頼るので、実測して固める） |
| 400 | `FAILED_PRECONDITION`（無料枠の地域制限） | `PermissionDeniedError` |
| 400 | その他 | `InvalidRequestError` |
| 402 | `RESOURCE_EXHAUSTED`（前払い残高ゼロ） | `QuotaExceededError`（再試行しない・フォールバックする） |
| 403 | `PERMISSION_DENIED` | `PermissionDeniedError` |
| 404 | `NOT_FOUND` | `NotFoundError` |
| 429 | `RESOURCE_EXHAUSTED` | `RateLimitError`。`details` に `RetryInfo.retryDelay` があれば `retry_after_seconds` に入れ、無ければ `Retry-After` ヘッダを見る（**[未検証]** どちらも資料に記載が無い） |
| 499 | `CANCELLED` | `OmniError`（再試行しない） |
| 500 / 503 / 504 | `INTERNAL` / `UNAVAILABLE` / `DEADLINE_EXCEEDED` | `ProviderOverloadedError`。ただし 500 の message が「input context is too long」なら `ContextLengthExceededError` [確認済: 資料の例文] |

> ⚠ 402 と 429 はどちらも `RESOURCE_EXHAUSTED` になる [確認済]。そのため、**HTTP ステータスで判定し、`status` 文字列では判定しない**。

### 7.7 Files API（大きなメディア）

[確認済] resumable 方式で、`POST /upload/v1beta/files`（start）→ 応答ヘッダの `x-goog-upload-url` に本体を送る（upload, finalize）→ `file.state` が `ACTIVE` になるまで `GET /v1beta/files/{id}` で確認する。上限は 1 ファイル 2GB、保持は 48 時間。

- 本体は `httpx` に**ファイルオブジェクトのまま渡して**ストリーム送信する（メモリに全部読み込まない）。
- `ACTIVE` になるまでの確認は、間隔を少しずつ延ばしながら行い、上限 120 秒（変更可）。`FAILED` なら `InvalidRequestError`。
- アップロード結果は `FileRef(provider="gemini", file_id, uri, mime_type)` として返し、`GeminiChatAdapter.upload_file()` で、利用者が自分で事前アップロードすることもできるようにする。

---

## 8. OllamaChatAdapter

### 8.1 方針

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| API | ネイティブの `POST /api/chat` を使う（OpenAI 互換の `/v1` は使わない） | [確認済] `/v1` では `num_ctx` を指定できず、`tool_choice` も非対応。ネイティブ API なら `think`・`keep_alive`・`format`（スキーマ）が使える |
| 接続先 | §5.5 の解決順 → `compat.resolve_ollama_base_url()` | [確認済] `OLLAMA_HOST` はスキームを省略でき（既定 http）、ポートも省略できる（既定 11434）。`0.0.0.0` は `127.0.0.1` に読み替える |
| 既定モデル | **既定値を持たない**（`default_model` を必須にする） | 利用者の手元にあるモデルが分からないため。`/api/tags` の結果から黙って選ぶことはしない |
| 単価 | ローカルモデルは 0.0。**名前が `:cloud` / `-cloud` で終わるモデルは `None`（不明）** | Ollama 経由のクラウドモデル（2026-11-07 時点で、師匠の Mac には `gpt-oss:120b-cloud` などがある [実測]）は無料ではないため。ADR-10 の「Ollama は常に 0」を修正したもの |

### 8.2 事前の能力確認（`/api/show`）

[確認済] `POST /api/show` の `capabilities` は、`completion` / `tools` / `vision` / `thinking` / `audio` などを返す。

- アダプターは、モデルごとに最初の 1 回だけ `/api/show` を呼び、結果をキャッシュする（プロセス内のみ）。
- `tools` を渡したのに `"tools"` が無ければ、送信前に `UnsupportedFeatureError` にする。画像を渡して `"vision"` が無い場合や、思考を指定して `"thinking"` が無い場合も同じ。
- [実測 2026-11-07・Mac・Ollama 0.17.7] `gemma4:e4b-mlx` の capabilities は `['completion', 'vision', 'audio']` で `tools` が無かった。`gpt-oss:120b-cloud` は `['completion', 'tools', 'thinking']`。モデルによって差が大きく、この事前確認は必要。

### 8.3 要求の写像

| 共通型 | Ollama | 備考 |
| :--- | :--- | :--- |
| `Role.*` | `role: system / user / assistant / tool` | [確認済] |
| `TextPart`（複数） | `content`（改行で連結） | `content` は文字列だけ |
| `MediaPart(type="image")` | `images: [base64...]` | [確認済] base64 のみ。`UrlRef` は `UnsupportedFeatureError` |
| `MediaPart(type="audio" / "video" / "document")` | `UnsupportedFeatureError` | ネイティブ API に、音声や文書を入力するフィールドは確認できなかった（capabilities に `audio` があるモデルでも同じ扱い） |
| アシスタントの `tool_calls` | `tool_calls: [{type:"function", function:{index, name, arguments(object)}}]`。Ollama が採番した id があれば `id` も付ける | [確認済] arguments は文字列ではなく **object** |
| `Role.TOOL` | `{role:"tool", tool_name, content}`。id が Ollama 由来なら `tool_call_id` も付ける | [確認済] 資料に載っているのは `tool_name` だけ。`tool_call_id` はソースにあるが、資料には無い |
| `tools` | `[{type:"function", function:{name, description, parameters}}]` | [確認済] |
| `tool_choice` | `auto` → そのまま、`none` → tools を送らない、`required` / ツール名 → `UnsupportedFeatureError` | [確認済] ネイティブ API に tool_choice は無い |
| `BuiltinTool.*` | `supported_builtins` は空。`agenerate` に直接渡すと `UnsupportedFeatureError` | [確認済] ローカルの `/api/chat` には、サーバー側で実行するツールが無い。代行は `ToolRunner`（§5.10） |
| `response_schema` | `format: <JSON Schema>` | [確認済] |
| `temperature` / `max_tokens` | `options.temperature` / `options.num_predict` | [確認済] |
| 固有の指定 | `ollama={"num_ctx", "keep_alive", "think", "options": {...}}` | [確認済] `think` は bool か、`/api/show` の `thinking.values` にある文字列 |

### 8.4 応答とストリーミング

- 非ストリームの場合: `message.content` → `content`、`message.thinking` → `reasoning`、`message.tool_calls[]` → `ToolCall`（id が無ければ SDK が採番する）[確認済]。
- ストリームの場合: **NDJSON** [確認済]。1 行ずつ JSON として読む。
  - `message.thinking` → `reasoning_delta`、`message.content` → `text_delta`。
  - `message.tool_calls` は、資料の例では完成した形で 1 チャンクに収まって届く → そのまま `tool_call` イベントにする。ただし「断片にならない」とは資料に明記されていないので、**同じ index のものが続いたら引数を結合する**防御を入れる。
  - `done: true` の行で `message_complete` を出す。
- `done_reason`: `stop` → `stop`（tool_calls があれば `tool_calls`）、`length` → `length`、それ以外（`load` / `unload` / 空文字）→ `unknown`。
- usage: `prompt_eval_count` → prompt、`eval_count` → completion、`prompt_eval_cached_count` があれば cached。
- `request_id` は無い（`None`）。

### 8.5 エラーの写像

| 状況 | 例外 |
| :--- | :--- |
| 接続拒否（Ollama が起動していない） | `ProviderConnectionError`。コンテナの中なら、`host.docker.internal` を 1 回だけ試す（§5.6） |
| 400 で、message に `does not support`（tools / thinking など） | `UnsupportedFeatureError` |
| 400 その他 | `InvalidRequestError` |
| 404（`model '...' not found`） | `NotFoundError` [実測: 本文 `{"error":"model 'no-such-model:1b' not found"}`, HTTP 404] |
| 429 | `RateLimitError` |
| 500 / 502 / 503 | `ProviderOverloadedError` |
| **ストリーム途中の `{"error": ...}` 行** | 最初のイベントの前なら上の写像、後なら `StreamInterruptedError` [確認済: HTTP は 200 のまま、行の中で error が届く] |

[実測 2026-11-07・Mac・Ollama 0.17.7] 手元の `gemma4:*-mlx` 系はどれも `{"error":"mlx runner failed: Error: unsupported architecture: ..."}` を **HTTP 500** で返した。ストリーム要求でも、200 ではなく最初から 500 で返った。これは一時的な障害ではなく、**何度送り直しても失敗する**。現在の写像（500 → `ProviderOverloadedError` → 再試行あり）では、無駄な再試行が 1 回発生する。message の `runner failed` を見て**再試行しない**扱い（`retryable=False` の `ProviderOverloadedError`、つまりフォールバックはするが再試行はしない）にする。

### 8.6 文脈長（重要な残留リスク）

[確認済: ソースのみ] `/api/chat` は、既定で文脈長 `num_ctx` を超えた古い履歴を**黙って切り捨てる**（`truncate` と `shift` の既定値が true）。エラーにはならず、切り捨てたことを知らせるフィールドも無い。既定の `num_ctx` は VRAM の量によって 4k / 32k / 256k と変わる。

- SDK は `ContextLengthExceededError` を**出せない**。代わりに、`prompt_eval_count` が `num_ctx`（指定されている場合）の 95% 以上なら `logging.warning` で「切り捨てられた可能性」を知らせる（縮退経路のログ）。
- 利用者が確実に気付けるように、`ollama={"num_ctx": ...}` の明示指定を README で推奨する。
- `truncate: false` を渡すとどうなるかは **[未検証]**（ソースにあって資料に無い）。今回は実装しない。

---

## 9. テスト方針

| 層 | 内容 | 実行条件 |
| :--- | :--- | :--- |
| unit | codec の純関数（写像表の 1 行ごとに 1 ケース）、例外の写像、SSE / NDJSON パーサー（`hypothesis` で任意の位置で分割しても結果が同じになること）、`RetryPolicy.next_delay`、`omni_tool` のスキーマ正規化、`ToolRunner`（偽の ChatCapability を使う） | 常に |
| contract | §6.3 の C-01〜09 を両アダプターに当てる（respx ＋ 記録した応答） | 常に |
| router | フォールバックの全分岐（例外の種類、組み込みツールでの飛ばし、ストリームの ADR-07、`model_map`）、計測プロキシ（フックの例外を握りつぶすこと、`None` コストの集計） | 常に |
| compat | `OLLAMA_HOST` の正規化表、パスの NFC 化、`safe_temp_file` | 常に（3 OS の CI で） |
| live | 実 API。`OMNI_LIVE_GEMINI=1` / `OMNI_LIVE_OLLAMA=<model>` のときだけ実行する。**記録した応答（fixtures）を更新する役目も持つ**（`--record`。記録時にキーと関係するヘッダは取り除く） | 手動 / 夜間 |

- CI: GitHub Actions で 3 OS × Python 3.11 / 3.12 / 3.13（ADR-05）。live テストは CI では実行しない。
- 型: `mypy --strict`（`src/` のみ）。lint: `ruff`。
- **fixtures の出所を記録する**: 各記録ファイルの横に `*.meta.json`（取得日・モデル名・SDK のバージョン・取得した機械）を置く。手で作った応答と、実際の API から記録した応答を区別するため。

---

## 10. 実装順序（マイルストーン）

各マイルストーンの終わりに、単体・契約テストがすべて通ることを条件にする。

| # | 内容 | 終了条件 |
| :--- | :--- | :--- |
| M0 | リポジトリの雛形（`pyproject.toml`、src レイアウト、ruff / mypy / pytest、3 OS の CI） | 空のテストが 3 OS で通る |
| M1 | `exceptions` / `types` / `capabilities` / `config` / `compat` | 型・設定解決・compat の単体テスト |
| M2 | `_http`（transport、`RetryPolicy`、SSE / NDJSON パーサー）、`_media` | パーサーの性質テスト、再試行のテスト |
| M3 | `OllamaChatAdapter`（非ストリーム → ストリーム → ツール → 能力確認） | 契約テスト C-01〜09（Ollama） |
| M4 | `GeminiChatAdapter`（非ストリーム → ストリーム → ツール＋thought signature → 組み込みツール → Files API） | 契約テスト C-01〜09（Gemini） |
| M5 | `router`（計測プロキシ、`FallbackChatAdapter`、`OmniClient`）、`pricing` | ルーターのテスト |
| M6 | `tools`（`omni_tool` / `ToolRunner` / 組み込みツールの代行。代行用の `SearchCapability` はテスト用の偽物で可） | Ollama で `WEB_SEARCH` を代行するループのテスト |
| M7 | live テストで fixtures を取り直し、§12 の [未検証] / [未実測] を潰す | 未検証の一覧が更新されている |

Ollama を先にするのは、ローカルで無料で試せることと、ワイヤ形式（NDJSON、完成した tool_calls）が単純で、共通の骨格を固めやすいためである。

---

## 11. 残留リスク（未解決の点と、その扱い）

| # | リスク | いまの扱い | 解除の条件 |
| :--- | :--- | :--- | :--- |
| R-01 | Gemini の `generateContent` が legacy 扱い | codec を差し替えられる構造にしておく | 公式がサンセット日を出したら、Interactions の codec を追加する |
| R-02 | Gemini の文脈長超過・RetryInfo・SSE 途中のエラーの形が未確認 | message の部分一致と、ヘッダの両方を見る防御的な実装 | M7 の live テストで実測する |
| R-03 | Ollama は文脈長の超過を黙って切り捨てる | warning ログ ＋ `num_ctx` の明示を推奨 | Ollama が切り捨ての通知を返すようになったら、例外に変える |
| R-04 | Chat 以外の 9 領域を検証していないので、抽象（`Capability` 基底・名前空間付き options）がそれらに合わない可能性 | 共通規約は最小限にとどめる | 2 領域目（候補: Search ＝ ToolRunner の代行先）を実装するときに見直す |
| R-05 | サーバー側ツールの実行結果（コード実行の出力・検索クエリ）を、共通型として出す項目が無い | `provider_state` にだけ保持する | 利用者から要望が出たら、`server_tool_results` を追加する |
| R-06 | Gemini の組み込みツールと関数の併用は Preview 機能 | 併用したときだけフラグを付ける | GA になったら注記を外す |
| R-07 | 師匠の Mac にある Ollama のローカルモデルでは、ツール呼び出しを実測できない（§8.5） | Ollama の tools の経路は、資料と記録した応答だけで検証する | **決定（2026-11-07）**: `gemma4:31b-cloud` を使う。**実施済み**: 応答を記録して契約テストの fixtures にし、live テスト 5 件（ツールループ・ストリーム・構造化出力・フォールバック・検索代行）が通った（§12.2）。ローカル実行の検証ではない点は残る |
| R-08 | Gemini `generateContent` の実測（当初は無応答・本文が空の 404 で実測できなかった） | **2026-11-07 に切り分け済み**: 原因はおもに**モデル**。`gemini-3.8-flash` はこのキーでは無応答のままタイムアウトし、`gemini-3.1-flash-lite` は SDK 経由でテキスト・ストリーム・並列ツール呼び出し（thought signature の往復を含む）が通った。空の 404 は一時的な現象で再現しなかった（原因は未確定）。Web 検索（google_search）は 429 quota 超過 | 既定モデルの扱いを師匠と決める。Web 検索の live 確認は quota の状況次第 |

---

## 12. 確認の記録

### 12.1 一次資料（2026-11-07 取得）

Gemini（`ai.google.dev` は `aistudio.google.com` へ移転済み）:
- https://aistudio.google.com/llms.txt
- https://aistudio.google.com/api-reference/generate-content/generate-content.md
- https://aistudio.google.com/api-reference/api-versions.md
- https://aistudio.google.com/docs/function-calling.md?generate-content=true
- https://aistudio.google.com/docs/thought-signatures.md?generate-content=true
- https://aistudio.google.com/docs/tool-combination.md?generate-content=true
- https://aistudio.google.com/docs/api-errors.md?generate-content=true
- https://aistudio.google.com/docs/files.md?generate-content=true
- https://aistudio.google.com/docs/file-input-methods.md?generate-content=true
- https://aistudio.google.com/docs/thinking.md?generate-content=true
- https://aistudio.google.com/docs/whats-new-gemini-3.6.md?generate-content=true
- https://aistudio.google.com/docs/models.md

Ollama:
- https://docs.ollama.com/api/chat ／ https://docs.ollama.com/openapi.yaml
- https://docs.ollama.com/api/streaming ／ https://docs.ollama.com/api/errors
- https://docs.ollama.com/capabilities/tool-calling ／ https://docs.ollama.com/capabilities/thinking
- https://docs.ollama.com/context-length ／ https://docs.ollama.com/faq
- https://docs.ollama.com/capabilities/web-search ／ https://docs.ollama.com/api/openai-compatibility
- https://github.com/ollama/ollama （`api/types.go`, `server/routes.go`, `envconfig/config.go` ― 資料に無い点の確認用。リリース版と一致するかは未確認）

### 12.2 実測（2026-11-07・師匠の Mac）

| 対象 | 結果 |
| :--- | :--- |
| Ollama のバージョン | 0.17.7 |
| `/api/show` の capabilities | `gemma4:e4b-mlx` / `gemma4:e2b-mlx`: completion, vision, audio ／ `gpt-oss:120b-cloud`: completion, tools, thinking |
| 存在しないモデルへの `/api/chat` | HTTP 404 `{"error":"model 'no-such-model:1b' not found"}` |
| `gemma4:*-mlx` への `/api/chat` | HTTP 500 `mlx runner failed: unsupported architecture`（ストリームでも 500） |
| Gemini `models.list`（このキー） | 成功。`gemini-3.8-flash` などを確認 |
| Gemini `countTokens` | 成功（HTTP 200） |
| Gemini `generateContent` | 当初は実測できず。後に切り分け: `gemini-3.1-flash-lite` はテキスト 10.9 秒・ストリーム 2.4 秒で成功、`gemini-3.8-flash` は 90 秒×2 回でタイムアウト、google_search は 429（quota 超過）。SDK の live テスト 3 件中 2 件（テキスト、並列ツール＋署名の往復）が通過 |
| Ollama `gemma4:31b-cloud` の `/api/chat`（記録は `tests/fixtures/ollama/`） | ツール呼び出しに `id`（`call_` + 8 文字）が付く（資料に無い）。ストリームではツール呼び出しが 1 件ずつ完成形で届く。ツール呼び出しで終わっても `done_reason` は `"stop"`。**`format` に JSON Schema を渡しても ```json のコードフェンスに包まれて返った** |
| SDK 経由の live テスト（`OMNI_LIVE_OLLAMA=gemma4:31b-cloud`） | 5 件通過（ツールループ、ストリーム＋構造化出力、到達不能な Gemini からのフォールバック、WEB_SEARCH 代行、接続失敗の例外） |

---

## 13. 実装との差分（M0〜M6 実装後・2026-11-07）

文書の正の向きに従い、実装で変えた点・決めた点を記す。仕様書 v3.1 への反映が必要なものは「仕様書」欄に記す。

| # | 内容 | 理由 | 仕様書 |
| :--- | :--- | :--- | :--- |
| I-01 | `ChatResponse.provider_state` を追加（`to_message()` がこれをアシスタントメッセージに引き継ぐ） | §7.4 の「parts を丸ごと保存」の置き場が応答側に必要だった | §4 に反映済み |
| I-02 | `ChatCapability.supported_builtins` を ClassVar ではなく、インスタンスで上書きできる属性にした | `FallbackChatAdapter` と計測プロキシが、内側のプロバイダの値を名乗る必要がある | §5 に反映済み |
| I-03 | 構造化出力の検証前に、応答全体を包むコードフェンスを 1 組だけ外す | Ollama の実測（§12.2） | — |
| I-04 | ストリームの途中でエラーが届いたら、それより前のイベントを先に返し、エラーは次の読み取りで送出する | チャンクの区切り方によって挙動（例外の種類）が変わらないように | — |
| I-05 | Ollama のストリームのツール呼び出しは、届いたものをそのまま `tool_call` イベントにする（同じ index の断片を結合する防御は入れなかった） | 実測で完成形が届くことを確認した | — |
| I-06 | `ToolRunner`: handler の無い `ToolDefinition` が呼ばれたら、ループを止めて `pending_tool_calls` として返す（「unknown tool」は定義に無い名前だけ） | 呼び出し側で実行したいツールと混在させられるように | §8.4 に反映済み |
| I-07 | `ToolRunner.run()`（同期）はツールを順に実行する。非同期のツールは `anyio.run` で実行する | 同期経路でスレッドを増やさないため | — |
| I-08 | `FallbackChatAdapter`: 呼び出し側の `model` は先頭のプロバイダにだけ渡し、2 番目以降は `model_map` に対応が無ければ `None`（＝そのアダプターの `default_model`） | 別プロバイダに同じモデル名を渡さない（§5.9 の具体化） | — |
| I-09 | Ollama の接続先: 引数と `OMNI_OLLAMA_BASE_URL` はそのまま使い、`OLLAMA_HOST` だけ Ollama 本体の規則で正規化する。`compat` の関数名は公開の `normalize_ollama_host` | 利用者が明示した URL は加工しない | §9.3 に反映済み |
| I-10 | `OllamaChatAdapter(api_key=...)` を追加（`Authorization: Bearer`） | リバースプロキシ越しの Ollama のため | — |
| I-11 | `ChatMessage.system/user/assistant/tool` の生成ヘルパーと `text()` を追加 | 利用側のコード量を減らす | — |
| I-12 | Gemini の契約テストは、資料の例から**手作り**した応答で行っている | R-08（実測できない） | — |
| I-13 | 非同期の Files API アップロードは、ファイルを 1MB ずつ流す（全体をメモリに載せない） | §5.8 のメモリ方針 | — |
| I-14 | 独立レビュー（別の文脈のエージェント、2026-11-07）の指摘 12 件を修正し、回帰テストを追加（`tests/unit/test_review_regressions.py`）。主なもの: Ollama ストリームで構造化出力の検証と `attempts` が抜けていた、ストリーム終端のスキーマ違反が `StreamInterruptedError` になっていた、外部ツールと内部ツールが同じターンに来ると内部ツールが実行されなかった、ストリームのメディアを 2 回目に空で送っていた | 作った本人のテストでは見つからなかった | — |
| I-15 | メディアのストリームは読んだ後に元の位置へ戻す。戻せないストリームは 1 回しか送れないので warning を出す。Gemini はアップロード済みのメディアを 47 時間キャッシュする | ToolRunner の各ターンやフォールバックで同じ履歴を何度も送るため | — |
| I-16 | `close()`（同期）は非同期 transport も使用不可にし、非同期クライアントが開いていれば `aclose()` を案内する warning を出す | 同期の close では非同期の接続を閉じられないため | — |
| I-17 | Gemini の既定モデルを `gemini-3.1-flash-lite` に変更（師匠の決定） | 実測: `gemini-3.8-flash` は同じキーで無応答 | — |
| I-18 | prefill（最後が assistant）を送信前に拒否しない | 実測: 3.1-flash-lite は受け付けた | — |
| I-19 | Gemini の契約テストを、実 API の記録（`tests/fixtures/gemini/`）に置き換えた。SDK が組み立てる並列ツール呼び出しの往復の要求が、実 API で 200 になった要求と**完全に一致**することを確認。記録できないケース（402・503・grounding 等）だけ手作りの応答を残した | R-08 の解消 | — |

---

## 14. Embedding（2026-11-07 追加スプリント）

師匠の指示: 「Embedding (Recommended)」、追加で「ローカル実装の埋め込みにも対応させて欲しい。../Mnemo/で標準的に使用している埋め込みを使いたい。」

### 14.1 アダプター

| アダプター | 方式 | 既定モデル | 確認 |
| :--- | :--- | :--- | :--- |
| `GeminiEmbeddingAdapter` | `batchEmbedContents`（100 件ずつ分割。上限は [未検証]） | `gemini-embedding-2`（3072 次元。`usageMetadata` を返す） | 記録＋live |
| `OllamaEmbeddingAdapter` | `/api/embed`（256 件ずつ分割） | なし（必須）。live は `nomic-embed-text`（768 次元） | 記録＋live |
| `FastEmbedEmbeddingAdapter`（provider_name `"fastembed"`） | プロセス内の **fastembed（ONNX）**。extras `[local]` | **`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`**（384 次元。Mnemo と同じ） | live（Mnemo のキャッシュを共有） |

### 14.2 共通の決まり（`EmbeddingCapability`）

| 決まり | 理由（実測） |
| :--- | :--- |
| 空リストは API を呼ばずに空の応答。空文字列は送信前に `InvalidRequestError` | Gemini は 400、Ollama は 200 で `embeddings: []`（件数がずれる） |
| 返ってきたベクトルの件数が入力と違えば `OmniError` | 件数のずれを黙って通さない |
| `normalize=True`（既定）で L2 正規化。`EmbeddingResponse.normalized` で示す | プロバイダ間・Mnemo との互換（Mnemo は L2 正規化済みを使う） |
| `task="query" / "document"` を追加。Gemini は `taskType` に写像、他は無視（debug ログ） | 検索の非対称性を共通の書き方で指定するため |
| `dimensions` は Gemini（`outputDimensionality`）と Ollama（`dimensions`）で有効。ローカルは固定次元なので、違う値なら `UnsupportedFeatureError` | |
| Ollama は送信前に `/api/show` で `embedding` 能力を確認 | チャット用モデルだと 404「pull してください」という紛らわしい応答になる |
| Ollama の `truncate` は既定 true（切り詰め）。false なら超過は `ContextLengthExceededError` | 実測で既定は黙って切り詰め |
| `OmniClient.embedding` も計測プロキシで包む。ローカルは単価 0 | テレメトリを Chat と揃える |

### 14.3 ローカル実装（Mnemo 互換）

- Mnemo の `src/mnemo/core/embedding.py` と同じく、fastembed の出力（未正規化）を L2 正規化する。初回の `embed` で読み込み（スレッド安全）、非同期版はスレッドで実行する
- 既定のキャッシュは OS 標準（platformdirs）の `.../omni_ai/models`。既存のキャッシュ（例: Mnemo の `~/.mnemo/models`）を `cache_dir=` に渡せば、同じモデルファイルを共有し、再ダウンロードしない。`local_files_only=True` でネットワークに出ない
- **実測（2026-11-07・Mac）**: Mnemo の `EmbeddingModel.batch_encode` と同じ 3 文で比べ、cos = 0.9999999、要素ごとの差は最大 1e-4。fastembed のバージョン違い（Mnemo 0.8.0 / 本 SDK の開発環境 0.9.1）による差と推測（未確認）
- fastembed は onnxruntime（バイナリ wheel）に依存するため、コア依存には入れず extras にした（ADR-02 の明確化・ADR-03 の範囲内）

### 14.4 命名（師匠の決定 2026-11-08）

プロバイダーの API ではない実装（Mnemo と同様の実装）の provider_name とクラス名は、**実際に処理を担うエンジン/サービス名**にする（`"fastembed"`、Web 検索なら `"ddgs"`。ddgs は既定で複数エンジンを横断するため、当初案の "duckduckgo" から師匠の決定で訂正）。「Mnemo」という名称はコードに出さない（由来の記録は設計書・合意ドキュメントに残す）。これに伴い `LocalEmbeddingAdapter`（`"local"`）を `FastEmbedEmbeddingAdapter`（`"fastembed"`）に改名し、定数 `MNEMO_CACHE_DIR` を削除した。単価表は `FREE_PROVIDERS = {"fastembed", "ddgs"}` を無料とみなす。

---

## 15. Search（2026-11-08 追加スプリント）

師匠の指示: Search を ddgs（Mnemo の標準）で。provider_name は「エンジン/サービス名」→ ddgs が横断検索と判明し「"ddgs"（横断のまま）」。

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| アダプター | `DDGSSearchAdapter`（provider_name `"ddgs"`、extras `[search]`） | API キー不要。Mnemo と同じライブラリ |
| 既定 | backend `"auto"`、region `"ja-jp"`、safesearch `"on"`、timeout 10 秒 | Mnemo の既定と同じ。auto は 1 エンジンが止まっても他で返る |
| 件数 | `max_results` を 1〜50 に丸める。URL の重複・URL 無しは除く | Mnemo の上限 50 |
| 結果 0 件 | 空の結果で返す（例外にしない） | 実測: `DDGSException("No results found.")` |
| 例外 | `RatelimitException` → `RateLimitError`、`TimeoutException` → `ProviderTimeoutError`、その他 → `ProviderOverloadedError`。`RetryPolicy`（既定 2 回・2 秒）で再試行 | ddgs の例外名で判定（ライブラリを import せずに写像できる） |
| 非同期 | `asearch` はスレッドで実行 | ddgs は同期ライブラリ |
| 計測 | `OmniClient.search` も計測プロキシで包む。`ai.tools` の検索代行もここを通るので、テレメトリに載る | |
| 単価 | 0（`FREE_PROVIDERS`） | |
| 残留リスク | 公式 API ではなく検索結果を読み取るライブラリなので、レート制限や仕様変更で止まりうる。Mnemo の `url_guard`（結果の URL の接続先判定）は取り込んでいない | 必要になったら、結果のフィルタを差し込める口を足す |

**実測（2026-11-08・Mac）**: live テスト 2 件通過。Ollama（gemma4:31b-cloud）＋ `BuiltinTool.WEB_SEARCH` を ToolRunner に渡すと、ddgs で実際に検索して、その結果から最新の Python の版を答えた（end-to-end）。

---

## 16. Rerank（2026-11-08 追加スプリント）

師匠の指示: 「進めてください」（ローカル Rerank）、既定モデル「bge-reranker-v2-m3-int8 (Recommended)」。

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| アダプター | `FastEmbedRerankAdapter`（provider_name `"fastembed"`、extras `[local]`） | fastembed のクロスエンコーダー。キー不要 |
| 既定モデル | `BAAI/bge-reranker-v2-m3-int8`（多言語。実体は `onnx-community/bge-reranker-v2-m3-ONNX` の `onnx/model_int8.onnx` 570,727,094 バイト） | 師匠の決定 |
| ライセンス | 元モデルは Apache-2.0（確認済み）。**変換版リポジトリにはライセンスの明記が無い**（未確認） | 残留リスク |
| スコア | モデルの出力はロジット（実測: 関連 ≈ +7、無関係 ≈ -11）。既定でシグモイドを通して 0〜1 に。`fastembed={"raw_scores": True}` で生の値 | クラウドの Rerank（0〜1）と揃える |
| 並び | スコアの降順。`top_n` で切る。空の文書・空のクエリ・`top_n < 1` は `InvalidRequestError`。文書が空リストなら空の結果 | |
| 読み込み | 埋め込みと共通の `LazyModel`（初回利用時に 1 回、スレッド安全）。既定キャッシュは OS 標準 | |
| 計測 | `OmniClient.rerank` も計測プロキシで包む | |

**実測（2026-11-08・Mac）**: 日本語のクエリに対し、日本語と英語の関連文書が上位・無関係な文書が最下位（スコア < 0.01）。読み込み（ダウンロード込み）17.9 秒、4 文書の再順位付け 0.08 秒。live テストで、キー不要の RAG（ddgs 検索 → fastembed 埋め込み → fastembed rerank）を end-to-end で通した。

---

## 17. DocParser（2026-11-08 追加スプリント）

師匠の指示: 「次へ進んでください」→ おまかせで DocParser を選んだ（Mnemo の標準: PDF は pypdf、Office / RTF は markitdown）。

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| アダプター | `PyPDFDocumentParser`（`"pypdf"`）・`MarkItDownDocumentParser`（`"markitdown"`）・`MimeRoutingDocumentParser`（MIME で振り分け）。既定構成は `providers.default_document_parser()`。extras `[docs]` | 命名はエンジン名（confirmed Skill）。結果の `meta.provider` は実際に処理したエンジン |
| 対応形式 | .pdf / .docx / .doc / .xlsx / .xls / .pptx / .ppt / .rtf（拡張子 → MIME の明示の表） | Mnemo と同じ集合。`mimetypes` は OS によって .docx を知らない |
| 入力 | Path / bytes / ストリーム。bytes とストリームは `mime_type` が必要（ファイル名があれば拡張子から判定）。`UrlRef` / `FileRef` / 素の str は受け付けない | markitdown の `convert(str)` は URL として取りに行くことがある（勝手にダウンロードしない方針） |
| 取り出せなかったとき | 例外にせず `ParsedDocument.empty_reason` に理由を入れる（暗号化・画像だけの PDF・変換失敗・本文が空）。そのとき `markdown` は空 | Mnemo の方針「取れなかった理由まで含めて返す」 |
| 字形の正規化 | 康熙部首・CJK 部首補助・CJK 互換漢字の 4 範囲だけ、文字ごとに NFKC（`omni_ai._text.normalize_cjk_forms`）。全角英数字などは変えない | PDF が「口」を U+2F1D で返すことがある（Mnemo の実測） |
| ストリーム | 読んだら元の位置に戻す | 同じ履歴を何度も送るため（§13 I-15 と同じ） |
| CI | CI に `[docs]` を入れて、実物の文書ファイルで契約テストを走らせる | 入れないと、文書解析のテストがまるごとスキップされていた |
| 仕様の変更 | `DocumentParserCapability.parse(mime_type=None)`（v3.0 は既定が `"application/pdf"`）。`ParsedDocument` に `mime_type` / `empty_reason` を追加 | 拡張子から判定できるため |

**実測（2026-11-08・Mac、markitdown 0.1.9 / pypdf 7.0.0）**: テスト用の docx / xlsx / pptx / PDF（本文あり・画像だけ・暗号化）を `tests/fixtures/docs/make_fixtures.py` で生成して確認。暗号化された PDF は、空パスワードで開けず、ページ数も読めない（`page_count=None`）。手元の文書 4 つを解析 → fastembed で埋め込み → rerank すると、「猫はどれくらい眠る？」に対して猫の睡眠のスライドが最上位になった（キー不要の RAG）。

---

## 18. MCP（2026-11-08 追加スプリント）

師匠の指示: 「OK.次、MCPに進みましょう」。Mnemo の標準 `mcp>=2,<3`（開発環境では mcp 2.3.0）。

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| アダプター | `MCPClientAdapter(name, command=[...] | url=...)`（provider_name `"mcp"`、extras `[mcp]`） | 公式 SDK はプロトコルの参照実装なので、ADR-02 の例外として extras で使う |
| トランスポート | stdio と streamable-http。SSE は扱わない | SSE は旧方式 |
| 開閉 | `async with` で開閉。開いたタスクと同じタスクで使い、閉じる | anyio のキャンセルスコープの制約 |
| コマンド解決 | stdio のコマンドは `PlatformCompat.resolve_executable` で解決（絶対パスと `sys.executable` はそのまま） | Windows の .cmd / .exe（仕様書 §9.5） |
| ツール一覧 | `alist_tools()` → `ToolDefinition`（入力スキーマは `normalize_json_schema` で正規化。ページングに対応・キャッシュ） | 実測: スキーマに `title` が付く |
| ツール呼び出し | `acall_tool()` → `MCPToolResult(text, is_error, structured, attachments)`。既定の待ち時間上限 120 秒 | Mnemo と同じ上限 |
| エラー | ツールのエラー（未知のツール・引数違反・ツール内の例外）は結果の `is_error`（例外にしない）。待ち時間切れ → `ProviderTimeoutError`、その他の MCPError → `InvalidRequestError`、接続の失敗 → `ProviderConnectionError`（ExceptionGroup を展開して原因を出す） | 実測と Mnemo の教訓 |
| 画像・音声 | `attachments` に分け、テキストに base64 を混ぜない。`for_model()` では添付を注記するだけ | Mnemo の教訓（base64 の漏れ） |
| サーバーの stderr | 既定でログファイル（OS 標準キャッシュ配下 `mcp/<name>.stderr.log`）に残す。`errlog=` で変更可 | 捨てると障害に気づけない（Mnemo の教訓） |
| ToolRunner | `atools()` で `OmniTool` の一覧を得て、そのまま `ai.tools.arun(..., tools)` に渡す。`tool_prefix` で複数サーバーの名前の衝突を避ける | 仕様書 Layer 3 |
| CI | `[mcp]` を入れて、テスト用 MCP サーバー（`tests/fixtures/mcp/demo_server.py`）を 3 OS で実際に起動する | |

**実測（2026-11-08・Mac）**: テスト用サーバーを stdio で起動して、ツール一覧・呼び出し・エラー・画像・待ち時間切れを確認。Ollama（gemma4:31b-cloud）が MCP の weather ツールを 2 回呼び、東京と大阪の天気を答えた（live）。mcp 2.x では FastMCP が `MCPServer` に改名され、streamable-http は `httpx2` を使う。

---

## 19. Judge（2026-11-08 追加スプリント）

師匠の指示: 「OllamaにJudgeモデルがあるはずなのでそれで試して」。AI は当初 LLM-as-a-Judge を提案していた（前提の訂正）。

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| アダプター | `OllamaJudgeAdapter`（provider_name `"ollama"`）。`POST /v1/systemone` | Ollama の decision モデル（Jev に倣った API の形）。一次資料: docs.ollama.com/capabilities/decision.md |
| 前提 | Ollama v0.35.0 以上、capabilities に `decision` を持つモデル | 師匠の Mac は Homebrew 版 0.17.7 が動いていた → 師匠の決定でアプリ版 0.40.6 に切り替え |
| モデル | 既定なし（必須）。live は `tev1:0.8b`（834MB）と `kev:0.8b-q8_0`（812MB） | 師匠の選択（nimble 9.5GB は見送り） |
| 型 | `NoulQuestion.criteria`（"false" / "true" の説明、省略可）を追加 | 実測: API は noul にも criteria を持つ（省略も可） |
| 結果の写像 | noul: value=bool（p≥0.5）・probability=p・distribution={true,false}。choice: value=選択肢・probability=その確率・distribution=全選択肢・confidence。score: value=段階番号の確率加重平均・distribution=段階名ごとの確率・probability=最も確からしい段階の確率・confidence | 実測の応答（score は legend と probabilities を返す） |
| 送信前の検査 | 質問 1〜64 件、choice 2〜26、score 2〜10、空の state | 資料と実測（サーバーも 400 を返す）。質問名の制限はローカルでは掛からないので検査しない |
| エラー | チャット用モデル → 400 "not a decision model" → `UnsupportedFeatureError`、未知のモデル → `NotFoundError`、その他の 400 → `InvalidRequestError` | 実測 |

**実測（2026-11-08・Mac、Ollama.app 0.40.6）**: tev1 / kev の両方で、根拠のある文脈は根拠の無い文脈より「根拠あり」の確率が高く、障害の文は深刻度 high と判定された。

### 19.1 Ollama のサーバーを 0.40.6 に替えて分かったこと

- **応答の `model` が `gemma4:31b-cloud` から `gemma4:31b` に変わった**（`remote_model` / `remote_host` も無くなった）。SDK が応答のモデル名を記録していたため、クラウドモデルを無料と誤判定していた → Ollama のアダプター（チャット・埋め込み・Judge）は、**呼び出し側が指定したモデル名を記録する**ように修正（回帰テストあり）
- それ以外の Ollama の live テスト（チャット・埋め込み・検索代行・MCP）は 0.40.6 でもすべて通過

---

## 20. Audio（2026-11-08 追加スプリント）

師匠の指示: 構成「合成: Gemini TTS」「認識: faster-whisper」→ 直後に「取り消し、TTSはMnemoと同じEdgeTTSにしてほしい。mnemo-slideで採用している。」→「edge-tts ライブラリを直接」。認識のモデルは「medium（1.5GB）」。

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| 構成 | 合成 `EdgeTTSAudioAdapter`（`"edge-tts"`）、認識 `FasterWhisperAudioAdapter`（`"faster-whisper"`）、束ねる `AudioRouter`。extras `[audio]` | エンジンごとにアダプターを分ける（命名ルール）。結果の `meta.provider` は実際のエンジン |
| 合成 | 既定の声 `ja-JP-NanamiNeural`、話速 -50〜+100%、声の高さ -50〜+50Hz（Mnemo の mnemo-slide と同じ）。出力は MP3 のみ。同期版は別スレッドの新しいループで実行 | edge-tts は非同期だけ。実測で MP3 |
| 合成のエラー | 存在しない声 → `NoAudioReceived` → `InvalidRequestError`（声の確認を促す） | 実測: 声の誤りがこの例外になる |
| 認識 | 既定は medium・CPU・int8（Mnemo と同じ）、`vad_filter=True`、`beam_size=5`。結果に `segments` / `language_probability` を追加 | Mnemo の core/transcribe.py |
| 入力 | Path / bytes / ストリーム（読んだら位置を戻す）。URL・素の str は受け付けない | 他の領域と同じ方針 |
| 仕様の変更 | `AudioCapability.speak(voice=None)`（v3.0 の既定 "alloy" は OpenAI 固有） | アダプターごとに既定の声が違う |
| 外部送信 | ⚠ 合成する文章は Microsoft のオンラインサービスへ送られる | edge-tts の仕組み |

**実測（2026-11-08・Mac）**: edge-tts で「猫は一日に十二時間から十六時間ほど眠ると言われています。」を合成（MP3、2.2 秒）→ faster-whisper medium で「猫は1日に12時間から16時間ほど眠ると言われています」（言語 ja・確率 1.00、5.7 秒の音声を 3.3 秒）。漢数字が算用数字になる以外は一致。日本語の声は Nanami / Keita の 2 つ。

**付記**: Gemini の TTS（gemini-3.8-flash-tts）は日本語で動くことを実測したが、師匠の決定で採用しなかった。Gemini の画像生成は 429（quota）。Ollama の音声合成（phonon）は v0.40.9 以上・英語のみ。

---

## 21. Image の調査（2026-11-08・実装せず）

師匠の指示: 「了解。Ollamaの調査から開始してください」、モデル「x/flux2-klein:4b（5.7GB）」。

| 経路 | 結果（Mac・Ollama.app 0.40.6） |
| :--- | :--- |
| `/api/generate`（width / height / steps） | 400 "image generation models are not currently supported" |
| `/v1/images/generations` | 404 |
| `/api/chat` | 400 "does not support chat" |
| CLI `ollama run x/flux2-klein:4b` | 同じエラー |

- 原因: リリースノート v0.32.6（2026-08-04）「Experimental image generation has been temporarily removed. Continue using 0.32.5」。v0.40.15 まで復活の記載なし
- 0.32.5 では decision モデル（§19、0.35.0 以上が必要）と両立しない
- Gemini の画像生成（gemini-3.1-flash-image）は 429（quota・課金の設定が必要）
- **結論: 現時点では ImageCapability を実装できる経路が無い。** Ollama が画像生成を戻すか、Gemini の課金が設定されたら再開する

---

## 22. Sandbox（2026-11-08 追加スプリント）

師匠の指示: 「では画像は保留。sandboxに移りましょう」、方式「ローカルの Docker」、Docker Desktop の起動「起動してよい」。Docker Desktop が新しいライセンスへの同意待ちで止まっていたため、師匠に同意を依頼（「同意しました」）。

| 項目 | 決定 | 根拠 |
| :--- | :--- | :--- |
| アダプター | `DockerSandboxAdapter`（provider_name `"docker"`）。docker CLI を subprocess で呼ぶ（Python の依存の追加なし） | CLI は Windows / macOS / Linux 共通。Docker Engine の HTTP API は Windows で名前付きパイプになり httpx で扱えない |
| CLI の解決 | `PlatformCompat.resolve_docker()`: PATH → Docker Desktop の既定の場所。見つけた bin を PATH の先頭に足す | 実測: macOS で CLI が PATH に無く、資格情報ヘルパーも同じ bin にあった（ADR-01: OS 固有の知識は compat に置く） |
| 隔離（既定） | `--network none`・`--user 65534:65534`・`--read-only`・tmpfs の `/work` と `/tmp`（64MB）・`--memory 256m`（swap 無し）・`--cpus 1`・`--pids-limit 64`・`--cap-drop ALL`・`no-new-privileges`・`--rm`・`--pull never` | 実測ですべて効くことを確認 |
| 時間切れ | `subprocess` の timeout → `docker kill <name>`。結果は `timed_out=True`・終了コード 137（例外にしない） | ToolRunner からモデルに結果として返せるように |
| 生成ファイル | 包みのスクリプトが、終了時に作業ディレクトリのファイルを tar.gz にし、その回だけの印を付けて標準出力に書く。`files`（名前 → 中身）と `artifacts`（v3.0 互換）で返す。合計 10MB まで | `--rm` と tmpfs ではコンテナから後で取り出せないため |
| 出力 | 標準出力・標準エラーは 1MB で切る。利用者のコードは base64 で包みに埋め込む | |
| イメージ | 既定 `python:3.12-slim`（標準ライブラリのみ）。`ensure_image()` で先に取得 | 実行中にダウンロードしない |
| 計測・ToolRunner | `OmniClient.sandbox` も計測。`ai.tools` の `CODE_EXECUTION` 代行はこれを使う | |

**実測（2026-11-08・Mac・Docker Engine 29.9.2）**: 起動 0.1〜0.4 秒。ネットワーク遮断（Network is unreachable・名前解決も不可）、ルートへの書き込み不可、600MB の確保で 137、63 個目で fork が止まる、docker kill は 0.1 秒でコンテナも残らない。Ollama（gemma4:31b-cloud）が `CODE_EXECUTION` で書いたコードを Docker で実行し、1000 未満の素数の和 76127 を答えた（end-to-end）。

**残留リスク**: コンテナはカーネルを共有するので、仮想マシンほどの隔離ではない（Docker Desktop では Linux VM の中で動く）。パッケージが要るコードは、利用者が自分のイメージを `image=` で渡す。

---

## 23. OS 依存の点検と CI の拡張（2026-11-08）

師匠の問い: 「今回の実装で、mac依存でLinux, Windowsで動作しない部分はある？」→ 点検の結果、OS の分岐は `compat.py` の中だけで、Mac でしか動かないコードは無い。ただし Mac でしか実物で確かめていない部分があった。師匠の指示「１，３を進めてコミット、プッシュしてください」で次を行った。

**1. CI の拡張**
- 3 OS × Python 3.11〜3.13 の全ジョブで `[dev,docs,mcp,local,search,audio]` を入れる（バイナリ wheel: onnxruntime・ctranslate2・PyAV・primp 等が各 OS で入るかを確かめる）
- `tests/unit/test_extras_smoke.py`: 各エンジンのライブラリが読み込めること、アダプターを作るだけではモデルを読み込まずネットワークにも出ないことを確かめる。CI は `OMNI_REQUIRE_EXTRAS=1` で「入っていなければ失敗」
- Linux のランナーでは、Docker の Sandbox を実物でテストする（`tests/live/test_live_sandbox.py`。隔離・メモリ超過・時間切れ）

**3. Docker の接続エラーを分かりやすく**
- Linux: Docker のソケットに権限が無い（docker グループに入っていない）→ `ProviderConnectionError`（`usermod -aG docker` の案内つき）
- Windows: 名前付きパイプ `//./pipe/docker_engine` に届かない（Docker Desktop が起動していない）→ `ProviderConnectionError`

**残る未確認（師匠の Windows 機での確認待ち）**
- Docker Desktop の CLI の場所 `%PROGRAMFILES%\Docker\Docker\resources\bin\docker.exe`（推測）
- Docker Desktop が Windows コンテナのモードだと動かない（Linux のイメージを使うため）
- 実 API・実物の live テストは、Mac 以外では Docker の Sandbox（Linux CI）しか流していない

---

## 24. インストールとコンフィグレーション（2026-11-08）

師匠の指示: 「インストールとコンフィグレーションの整備」（手順と前提の確認・設定ファイルからの組み立て・設定の一覧と検証・モデルの管理・それらを案内するメニュー方式のインストーラ）。決定: 入口は `omni-ai` コマンド＋メニュー、API キーは別の秘密ファイル、自動導入は Python 側だけ。

| 部品 | 内容 |
| :--- | :--- |
| `omni_ai.settings` | `config.toml`（共有可）と `secrets.toml`（権限 600）。OS 標準の場所（`OMNI_CONFIG_DIR` で変更可）。`[client.<機能>]` の厳密なスキーマ（未知のキーは誤り）と、場所つきの検証。原子的な書き込み。TOML の書き出しは自前（`models` だけインライン表） |
| `omni_ai.config` | 解決の順に `secrets.toml` を追加（引数 → OMNI_* → 各社の環境変数 → secrets.toml → config.toml） |
| `omni_ai.builder` / `OmniClient.from_config()` | 設定から chat（フォールバックの順）・embedding・rerank・search・doc_parser・judge・audio・sandbox を組み立てる。誤りは `ConfigurationError`。MCP は対象外 |
| `omni-ai doctor` | Python・設定（権限・検証）・extras・Gemini のキー・Ollama（到達・判定に必要な版・設定が使うモデル）・Docker（CLI・デーモン・イメージ）。設定が使うものだけを失敗にする |
| `omni-ai models` | 一覧・容量の確認・取得・削除。取得の前に容量を表示して確認。共有 blobs は `.refs` を見て、自分だけが参照するものだけを消す |
| `omni-ai`（メニュー） | 診断・プロバイダー（キーは getpass で入力）・機能の構成（Ollama のモデルは capabilities で用途に合うものだけを並べる）・extras の導入（pip、無ければ uv）・モデル・設定の確認 |
| 手順書 | `docs/install.md` |

**実測（2026-11-08・Mac）**: メニューで chat（Ollama / gemma4:31b-cloud）・judge（tev1:0.8b）・embedding（Ollama / nomic-embed-text）を設定 → `omni-ai doctor` がすべて ✓ → `OmniClient.from_config()` で 3 機能が実物で動いた。メニューで Chat のモデルに埋め込みモデルまで並んでいた問題を、実際に操作して見つけて直した。


## 25. Chat プロバイダーの追加（xAI・Agnes・OpenRouter・Ollama Cloud・OpenAI 互換サーバー）（2026-11-08）

師匠の指示: 「../MnemoにあるELLM以外のプロバイダーに対応して」。決定: Chat のみ／手元の OpenAI 互換サーバーも含める（名前は `openai-compatible`）／Ollama Cloud はネイティブ API／実物での確認はせず偽物のテストだけ。

| provider_name | アダプター | 接続先（既定） | キー | 構造化出力の既定 |
| :--- | :--- | :--- | :--- | :--- |
| `xai` | `XAIChatAdapter` | `https://api.x.ai/v1` | 必須 `XAI_API_KEY` | json_schema |
| `agnes` | `AgnesChatAdapter` | `https://apihub.agnes-ai.com/v1` | 必須 `AGNES_API_KEY` | json_object（json_schema は未確認） |
| `openrouter` | `OpenRouterChatAdapter` | `https://openrouter.ai/api/v1` | 必須 `OPENROUTER_API_KEY` | json_schema |
| `openai-compatible` | `OpenAICompatibleChatAdapter` | なし（必須。`/v1` の有無を揃える） | 任意 | json_schema |
| `ollama-cloud` | `OllamaCloudChatAdapter` | `https://ollama.com`（ネイティブ `/api/chat`） | 必須 `OLLAMA_API_KEY` | （Ollama の format） |

- **共通の codec**（`providers/openai_compatible/codec.py`、I/O なし）: メッセージ（system / user / assistant＋tool_calls / tool）、画像（data URL または URL）、tools と tool_choice（auto / none / required / 関数名）、構造化出力（json_schema / json_object＋指示文 / prompt＝指示文だけ）、SSE（`[DONE]` 無しでも finish_reason があれば完了、ツール呼び出しの断片を index ごとに組み立て、本文の途中のエラー）、usage（推論トークンを completion から分ける・キャッシュ）、推論（`reasoning_content` / `reasoning`）、エラーの対応づけ（402→QuotaExceeded、文脈長→ContextLengthExceeded）
- **素通し**: provider_options の自分の名前空間の `top_p` / `seed` / `stop` / `reasoning_effort` など、と `extra_body`
- **既定のモデルを持たない**（Gemini 以外）。設定の検証で `models.<名前>` が無ければ誤り
- **OpenRouter の帰属ヘッダーは付けない**（付けると公開ランキングに集計される。必要なら `extra_headers=`）
- **応答の model ではなく指定した名前を記録**（ルーティング先の名前で返ることがあるため。単価の照合を安定させる）
- **Ollama Cloud** は既存の Ollama codec に provider 名を引数で渡して共用。/api/show の能力確認とコンテナ向けの接続先切り替えはしない。無料扱いにしない
- 環境変数の `OMNI_<P>_<K>` は、名前のハイフンを `_` にする（`OMNI_OLLAMA_CLOUD_API_KEY`）
- 設定: `[xai]` `[agnes]` `[openrouter]` `[ollama-cloud]` `[openai-compatible]`（`base_url` / `api_key`。キーは secrets.toml）。`doctor` はキーの有無と、OpenAI 互換サーバーの `/models` への到達を見る。メニューはキーの入力・接続先・Chat の順番とモデル（一覧が 30 件以下なら選択、それ以外は手入力）

**実測（2026-11-08・Mac）**: キー無しで試せる OpenAI 互換サーバーとして、ローカルの Ollama 0.40.6 の `/v1`（gemma4:31b-cloud）に `openai-compatible` を繋いだ。テキスト・usage・ツール呼び出しの往復・ストリーム（本文とツール呼び出し）・非同期・構造化出力（3 方式）が動いた。`config.toml` → `omni-ai doctor`（`/v1/models` に到達）→ `from_config()` も通った。
- 見つけたこと: **json_schema を送っても、サーバーが黙って無視して地の文を返した**（"21 degrees C"）。→ どの方式でもスキーマを先頭の system の指示文で添えるように変更（OpenRouter も非対応モデルでは response_format を捨てる、という先行の記録とも合う）
- live テスト: `tests/live/test_live_openai_compatible.py`（`OMNI_LIVE_OPENAI_COMPATIBLE=<base_url>` と `..._MODEL`）

**未確認**: xAI・Agnes・OpenRouter・Ollama Cloud は実物に繋いでいない（キーが無い）。契約テストの応答は OpenAI の公開仕様に沿った合成データ。各社の usage の有無、推論の項目名、ツール呼び出しの分割の仕方、Agnes の json_schema 対応、Ollama Cloud の /api/show の有無は、実物で確かめるまで推測。
