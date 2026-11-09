# Mnemo の独自実装を OmniAISDK に置き換える — 調査レポート

- 作成: 2026-11-09 JST（OmniAISDK main `1d017c6` と、同日時点の ../Mnemo の作業ツリーを読んだもの）
- 依頼の原文:
  - 「OKでは、../Mnemo/の実装を確認し、本プロジェクトでMnemoのLLMを完全リプレースできるか、そのためには何をしなくてはならないかを手ってい調査。レポートして。」
  - 「embedding, tts, web search , web fetch など本SDKが提供する機能でMnemoが独自実装していることは多岐にわたる。LLMだけで見ずに、本SDKが提供している機能で、Mnemoが独自実装しているものを洗い出し、それをリプレースするために何が必要かの完全なレポートをmd型式で作成、報告すること」
- 調べ方: 両方のリポジトリのソースを読み、grep で数えた。**本物のキー・本物の ELLM・本物の音声やページで動かして確かめてはいない**（この文書の判定はすべてコードを読んだ結果）。

---

## 0. 結論

**いまは完全な置き換えはできない。** ただし領域によって状況がかなり違う。

| # | 領域 | Mnemo の独自実装 | OmniAISDK の対応 | 判定 |
|---|---|---|---|---|
| 1 | Chat / LLM | `src/mnemo/llm/`（約 5,000 行・9 プロバイダ）＋ wiseman の `OllamaClient`＋ mu の `ollama` 直呼び | Chat 7 種（gemini・ollama・ollama-cloud・openai-compatible・xai・agnes・openrouter） | **足りない部分が多い**: ELLM が無い。ストリームの中でツールを回す仕組みも、スキーマ無しの JSON モードも、文脈長の取得も無い |
| 2 | 埋め込み | `core/embedding.py`（fastembed・MiniLM-L12-v2・384 次元） | `FastEmbedEmbeddingAdapter`（同じモデル・同じ正規化） | **少し足せば置ける** |
| 3 | Web 検索 | `tools/web_search.py`（ddgs の文と画像）・`core/ellm_search.py`・wiseman の `ddg`/`ellm`/`academic`/`library` | `DDGSSearchAdapter`（文だけ）・Gemini の組み込み検索 | **一部だけ置ける**: DDG の文検索は置ける。ELLM 検索・画像検索・学術 API は SDK に無い |
| 4 | Web 取得 | `tools/web_fetch.py`（1,074 行）・wiseman `fetch.py`（187 行） | **無い**（Capability そのものが無い） | **SDK に新しい機能が要る** |
| 5 | 文書解析 | `core/markitdown_converter.py`（markitdown＋pypdf） | `MarkItDownDocumentParser`・`PyPDFDocumentParser`（読める拡張子は同じ） | **ほぼ置ける**。出力が同じか確かめる必要がある |
| 6 | 音声認識 | `core/transcribe.py`（faster-whisper medium・ffmpeg・独自 VAD・進捗）・`mnemo_recall`（リアルタイム・OpenVINO） | `FasterWhisperAudioAdapter`（medium・内蔵 VAD） | ファイルの文字起こしは**足せば置ける**。Recall は**対象外** |
| 7 | 音声合成 | 本体には無い。`mnemo-slide-engine` が外部の EdgeTTS **MCP サーバー**を呼ぶ | `EdgeTTSAudioAdapter`（MP3 のみ） | **足りない**: SDK には読み上げの記法（強調・間など）も WAV 出力も無い |
| 8 | 画像生成 | `tools/image_gen/`（ELLM の画像生成・バックエンドを差し替えられる） | `ImageCapability` の抽象だけ。**実装が 0 本** | **SDK に実装が要る** |
| 9 | MCP クライアント | `core/mcp_client.py` ほか（約 4,100 行。複数サーバー・SSE・OAuth・elicitation・再接続） | `MCPClientAdapter`（1 接続・stdio と streamable-http） | **差が大きい** |
| 10 | Rerank・Sandbox・Judge | Mnemo に対応する実装は見つからなかった | SDK にある | 置き換える対象が無い。新しく使える機能という扱い |
| 11 | 横断（設定・計測・切り替え） | プラグイン宣言・`ProviderProxy`・`_CallMeter`・provisioning | `OmniConfig`・`Instrumented*`・`TelemetryTracker`・`FallbackChatAdapter` | Mnemo 固有のものは **Mnemo に残す**。計測は SDK のものを使える |

**いちばん大きい障害は ELLM。** Mnemo は Chat・Web 検索・画像生成の 3 つを ELLM（`llmproviderlib`）経由で使っているが、OmniAISDK には ELLM が 1 つも無い。その次に大きいのは、SDK に Web 取得の機能そのものが無いこと。

**置き換えの方針（推奨）:**

- Mnemo 側に薄い包み（例: `OmniProvider(LLMProvider)`）を作る。こうすれば、200 箇所を超える呼び出し側を変えずに済む。
- 不足している部品は SDK に足す。
- Mnemo 固有の方針は Mnemo に残す（プラグイン・接続先の判定・日本語の説明・インシデントの記録）。
- 置き換えは領域ごとに段階を踏んで進める。

---

## 1. 調べた範囲

### OmniAISDK 側

OmniAISDK が提供するものはすべて調べた。

- **10 の Capability:** Chat、Judge、Image、Audio（合成・認識）、Embedding、Rerank、Search、DocumentParser、Sandbox、MCP。
- **横断の部品:**
  - `ToolRunner`
  - `router.py`（`Instrumented*`・`FallbackChatAdapter`・`TelemetryTracker`）
  - `settings.py` と `builder.py`（設定）
  - `pricing.py`
  - `cli`
- **アダプター:**
  - `providers/` の下の 15 種: gemini・ollama（Chat と Judge）・openai_compatible（xai・agnes・openrouter・ollama-cloud を含む）・fastembed（埋め込みと rerank）・ddgs・markitdown・pypdf・faster_whisper・edge_tts・docker・mcp。
  - SDK のソースは約 9,900 行、テストは約 5,100 行。

### Mnemo 側

- `src/mnemo/` の全体。
- `mnemo_recall/`。
- `companions/`。
- `vendor/mu`（ollama を import しているかだけ確認した）。

### 対象から外したもの

次のものは SDK の機能と重ならないので、置き換えの対象から外した。

- Mnemo 自身が提供している MCP サーバー（`src/mnemo/mcp/*`: browser・discord・email ほか）
- 保存層（`storage/embedding_store.py` など）
- 知識グラフの抽出（`document_processor` の KG）
- PDF の段落分割の規則（`pdf_split.py`）

---

## 2. 領域ごとの対照と不足

### 2.1 Chat / LLM

#### Mnemo の実装

**`src/mnemo/llm/`（約 5,000 行）**

- **抽象（`base.py`）:** `LLMProvider` が次のものを持つ。
  - 属性: `model_name`、`context_window`
  - `chat(messages, tools, tool_executor, max_tool_rounds=10) -> AsyncIterator[ChatChunk]`。**ツールのループはプロバイダの中で回る。**
  - `generate_json` / `generate_json_sync`（スキーマを受け取らない。温度の既定 0.0）
  - `generate_text` / `generate_text_sync`（温度の既定 0.7）
  - `describe_image(data, mime_type, prompt)`
- **メッセージ:**
  - `Message(role=system|user|model, parts=[TextPart|FunctionCallPart|FunctionResponsePart|ImagePart])`
  - `ChatChunk(type=token|tool_call|tool_result|error|reset)`
- **プロバイダ:**

  | プロバイダ | 中身 |
  |---|---|
  | xai・agnes・openrouter・ollama・ollama-cloud・custom | OpenAI SDK の上に共通実装 `openai_compat.py`（755 行） |
  | gemini | google-genai（537 行） |
  | ellm-lib | `llmproviderlib`（`ellm_lib.py` 602 行＋`_ellm_common.py` 391 行＋`_ellm_auth.py`） |
  | scripted | 試験用 |
  | unconfigured | 未設定のときの空の実装 |

- **生成の口:**
  - `factory.create_provider(provider, model, base_url, api_kind)` が唯一の生成点。
  - その下にプラグイン宣言（`plugins/llm_*/mnemo_plugin.json` の `llm_providers`）、`registry.py`、`builders.py`。
  - 閉じたプラグインは `LLMNotConfiguredError` で断る。
- **実測で入れた手当て**（置き換えで**失ってはいけない**もの）:
  - **上流が死んでいるときの冷却:** `DeadUpstreamGate`（30 秒・`MNEMO_LLM_DEAD_UPSTREAM_SECONDS`）。
  - **ストリームの無音打ち切り:** 最初の塊のあと 60 秒（`MNEMO_LLM_STREAM_IDLE_SECONDS`・エラー コード `llm_stream_idle`）。
  - **接続の時間設定:** connect 15 秒・read 300 秒・`max_retries=1`。
  - **`json_object` が拒まれたとき:** 指定を外して打ち直す。ただし接続エラーのときは打ち直さない。
  - **JSON の寛容な取り出し:** `parse_json_response` / `extract_json_block`。失敗したら匿名化して記録に残す。
  - **ツールの回数が上限に達したとき:** 要約で締める（コード `max_rounds_fallback` / `max_tool_calls`）。
  - **空の応答:** エラーにする（コード `empty_response`）。
  - **ツール結果の画像:** LLM に見せ直す（`llm_images`・`image_base64`・`fetched_images`・`binary_attachments`）。
  - **tool_call_id の合成:** `call_{name}` / `call_{name}-{n}`。
  - **Gemini の `thought_signature`:** 保持する。
  - **`openai.resources.chat`:** 先に import して温めておく。
  - **文脈長の取り方:** `models.retrieve`（5 秒で打ち切り）→ 静的表 → OpenRouter の `/models` のキャッシュ → custom の `meta.n_ctx`。
  - **モデル一覧:** プロバイダごとに `list_models` を持ち、取れないときの予備の一覧もある。
- **ELLM 固有の手当て:**
  - `_MNEMO_TOOL_RULES`
  - 呼び名の目印（`yourname`）
  - FSC-3 の告知の差し替え
  - BR-1 のインシデントの捕捉
  - `raw` / `parsed_blocks`
  - lib が打ち直すときに出す `reset` の塊
  - 背景処理の `max_tokens=8192`
  - 壊れた封筒（応答の外側の形）での lib の打ち直し
  - `get_token` / `aget_token`（画像生成も使う）
- **運用の部品:**
  - `ProviderProxy`（動かしたまま `switch()` で切り替える・`_CallMeter` で数える）
  - provisioning（PONG の疎通確認・`recover_llm_if_unconfigured`）

**`src/mnemo/` 全体の呼び出し数**（`src/mnemo/llm/` の外。grep の件数）

| 呼び出し | 件数 |
|---|---|
| `generate_json(` | 47 |
| `generate_json_sync(` | 6 |
| `generate_text(` | 3 |
| `generate_text_sync(` | 5 |
| `describe_image(` | 7 |
| `.chat(` | 34 |
| `context_window` | 11 |
| `model_name` | 92 |

- `create_provider` を直接使っているのは 3 箇所: `agents/mu/mnemo_l0.py`、`agents/wiseman/llm/mnemo_client.py`、`api/system.py`。
- LLM 関係の試験は 43 ファイル。

**LLM 層の外にある別の経路（これも置き換えの対象）**

- `agents/wiseman/llm/ollama_client.py`（233 行）:
  - httpx を直接使い、`/v1/chat/completions` に `response_format=json_schema` を付けて呼ぶ。
  - 受信は「コードフェンスを剥がす → Pydantic で厳格に検証 → 1 回打ち直す → `LLMSchemaError`」の順。
  - 429・5xx は指数バックオフで打ち直す。
  - 使っているのは `bench/baseline.py`、`bench/l3.py`、`resummarize.py`、`wiseman/llm/__init__.py`。
- `vendor/mu/mu/l0.py`: 公式の `ollama` クライアントを import している。`--llm mnemo` のときは `mnemo_l0.py` から `create_provider` を通る。**この先の経路は調べていない。**

#### OmniAISDK の現状

- **`ChatCapability`:**
  - `generate` / `agenerate` / `stream` / `astream`
  - 引数: `messages`、`model`、`temperature`、`max_tokens`、`tools`、`tool_choice`、`response_schema`（Pydantic）、`timeout`、`**provider_options`
- **型:**
  - `ChatMessage`（role は system / user / assistant / tool、`tool_calls`、`tool_call_id`、`provider_state`）
  - `ChatResponse`（`content`、`parsed`、`reasoning`、`tool_calls`、`citations`、`finish_reason`、`meta`）
  - `StreamEvent`（`text_delta` / `reasoning_delta` / `tool_call` / `citation` / `message_complete`）
- **`BaseChatAdapter`:** httpx の上に sans-IO のコーデックを載せる作り。`RetryPolicy` で打ち直す。
- **`ToolRunner`:**
  - 外側でツールを回す。**ストリームしない**（`tools.py:203` は同期、`:255` は非同期。どちらも応答の完成を待つ）。
  - 上限を超えると `ToolLoopLimitError` を投げる。
- **組み込みツール:** Gemini だけが `WEB_SEARCH`・`CODE_EXECUTION` を持つ。
- **Gemini の思考署名:** `provider_state` で引き継ぎ、無いときはダミーの署名を入れる（`gemini/codec.py:47`）。
- **ELLM:** 無い（`ellm` / `dx-ai` への言及が 0 件）。
- **Judge:** `OllamaJudgeAdapter` がある。Mnemo 側に相当する呼び出しは見つけていない。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| C1 | ELLM が無い（Chat・検索・画像） | ELLM のアダプターを作る（下の §2.12） | SDK（または別パッケージ） | L |
| C2 | ストリームしながらツールを回せない | `ToolRunner.astream()` を足す。`tool_call` / `tool_result` / `error` / `reset` のイベントを流す。上限に達したら要約で締める処理と、ツール結果の画像を見せ直す処理は呼び出し側から差し込める形にする | SDK と Mnemo の包み | M |
| C3 | スキーマ無しの JSON モードが無い | `response_format="json"`（json_object）の指定、拒まれたら指定を外して打ち直す処理、寛容な JSON の取り出し（`extract_json`）を足す | SDK | S |
| C4 | `describe_image` が無い | 画像の部品を付けたメッセージで `generate` を呼べば作れる。プロバイダごとに画像の入力が通るか確かめる | Mnemo の包み | S |
| C5 | 文脈長・モデル一覧の API が無い | `model_info(model) -> {context_window, ...}` と `list_models()` を足す。予備の静的表と OpenRouter の `/models` のキャッシュも持つ | SDK | M |
| C6 | 無音の打ち切り・死んだ上流の冷却が無い | `_http/streams` に最初の塊のあとの無音の打ち切りを足す。冷却（回路遮断）は router の包みとして足す | SDK | S〜M |
| C7 | メッセージの形が違う | `Message`/`Part` ⇔ `ChatMessage` の変換。`model` ⇔ `assistant`、FunctionCall/Response ⇔ `tool_calls`/`tool`、tool_call_id の合成 | Mnemo の包み | S |
| C8 | エラー コード・日本語の文 | SDK の例外を `ChatChunk(type=error, code=...)` に写す表 | Mnemo の包み | S |
| C9 | 動かしたままの切り替え・計測 | `ProviderProxy` は残す。`_CallMeter` を `InstrumentedChat` に寄せるかは任意 | Mnemo | S |
| C10 | wiseman の `OllamaClient` | ollama アダプターの `response_schema` に置き換える。予算・`CallLogger`・1 回の打ち直しは wiseman に残す | Mnemo | S |
| C11 | Gemini の思考署名 | SDK の引き継ぎ方（`provider_state`＋ダミー）で、Mnemo の保持と同じ結果になるか確かめる | 確認 | S |
| C12 | `openai` / `google-genai` への依存 | すべてのプロバイダを置き換えたあとで依存から外す | Mnemo | S |

---

### 2.2 埋め込み

#### Mnemo の実装

**`core/embedding.py`（393 行）**

- **モデル:** fastembed＋`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`（384 次元）。
- **出力:** L2 正規化して float32 に直す。sentence-transformers の `normalize_embeddings=True` と数値が合うようにするため。
- **キャッシュ:** `~/.mnemo/models/`。huggingface_hub の置き方で入っているかを見て判定する（`_has_cached_model`）。
- **トークナイザの使い回し**（`_donor_tokenizer`）:
  - 2 本目のモデルを開くと、`Tokenizer.from_file` が GIL を約 1.2 秒握り、イベントループが止まる（票 `20261108_Status_Load_Onset_Stalls_Health`、`20261028_Tokenizer_Deserialization_Holds_GIL`）。
  - それを避けるため、2 本目は 1 本目のトークナイザを使い回す。
- **インタフェース:**
  - `load` / `encode` / `batch_encode` / `is_loaded`。返り値は `np.ndarray`。
  - 包みとして `EmbeddingProxy` がある。
- **利用箇所:** 27 ファイル・134 行。主なもの:
  - 会話ターン（`turn_embedding.py`）
  - 文書（`document_processor.py`）
  - 横断検索
  - 重要度の作り直し
  - wiseman のライブラリ
- **保存:** `storage/embedding_store.py` が `(N, 384)` の npy をスレッドごとに置く（対象外）。

#### OmniAISDK の現状

- **`FastEmbedEmbeddingAdapter`:**
  - **既定のモデルは同じ。** L2 正規化する。
  - 引数: `cache_dir`、`threads`、`local_files_only`、`batch_size`。
  - `load()` を持つ。
- 返り値は `EmbeddingResponse.vectors: list[list[float]]`（Python の float）。
- `task`（query / document）・`dimensions` を受け取る。
- 計測用の `InstrumentedEmbedding` がある。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| E1 | 返り値が Python の list で、numpy ではない | numpy で返す道を足す（`embed_array()`、または `provider_options` の `output="numpy"`）。無ければ包みで `np.asarray(dtype=float32)` に直す（大きな束では複製の費用がかかる） | SDK | S |
| E2 | トークナイザの使い回しが無い | アダプターを 1 つ共有すれば済むか確かめる。2 本目の実体が要る使い方があれば、使い回しの機能を SDK に足す | SDK | S |
| E3 | **数値が同じか** | 既存の npy と同じベクトルになるか、実際のターン・文書で比べる（余弦が 1 から 1e-6 以内）。ずれると全件の作り直しになる（ターン 12,040 件以上と文書。重要度の作り直しも約 15 分かかる） | 確認 | S |
| E4 | キャッシュの場所 | `cache_dir=~/.mnemo/models` を渡す。オフラインでの判定は `local_files_only` で代わりになる | Mnemo の包み | S |
| E5 | 起動時の温めの順番 | `startup-warmup` の直列の順番は Mnemo に残す | Mnemo | — |

---

### 2.3 Web 検索

#### Mnemo の実装

- **`tools/web_search.py`（305 行）:**
  - ddgs の `text` と `images`。`region`、`safesearch` を設定から渡す。
  - 結果に**接続先の判定**（`core/url_guard.py` 203 行。DoH を含む）を当てる。
  - 画像を取ってくる。上限は同時 4 本・1 枚 10MB・合計 20MB。
- **`core/ellm_search.py`（209 行）:**
  - ELLM のサーバー側の検索。lib の `search` / `asearch` を呼ぶ。
  - lib の例外を `EllmSearchError.reason` に写す（8 種）。
  - **ほかの検索に切り替えない**（E6）。
  - 時間の上限は 15 秒。
- **wiseman の `search/`:**
  - `ddg.py`: 呼び出しの最小間隔・指数バックオフ・`cancel_check`。
  - `ellm.py`
  - `academic.py`（559 行）: Crossref・J-STAGE・OpenAlex・DOI。抄録を本文として返す。
  - `library.py`: 自分で溜めた本文を埋め込みで引く。外へは問い合わせない。
- **利用箇所:** 19 行。本体の `web_search`、`research_probe`、wiseman、mu の包み。

#### OmniAISDK の現状

- `DDGSSearchAdapter`:
  - **文の検索だけ。**
  - 引数: `region`、`safesearch`、`backend="auto"`、`RetryPolicy`。
  - 返り値は `SearchItem(title, url, content, score)`。
- Gemini の組み込み検索（`google_search`）。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| S1 | ELLM の検索が無い | `EllmSearchAdapter`（§2.12） | SDK（または別パッケージ） | S（C1 の上に載る） |
| S2 | 画像検索が無い | `search_images()`、または画像用の結果の型（`image_url`、`thumbnail`、`width`、`height`、`source`）を足す | SDK | S〜M |
| S3 | 学術 API（Crossref・J-STAGE・OpenAlex） | **Mnemo に残すのを推奨**（抄録を引用として照合するなど、wiseman の役目に密着している）。SDK のアダプターにしてもよい | 方針 | (M) |
| S4 | 接続先の判定 | Mnemo の方針なので Mnemo に残す。SDK の結果に当てる | Mnemo | S |
| S5 | 最小間隔・中断の検査点 | wiseman の包みに残す。または SDK に `min_interval` を足す | Mnemo | S |
| S6 | 項目名の違い（`snippet` ⇔ `content`） | 包みで写す | Mnemo | S |
| S7 | `ddgs` の既定の違い | Mnemo は `backend` を指定しない。SDK は既定で `"auto"`。wiseman の `region` は `wt-wt`。設定値をそろえる | Mnemo の包み | S |

---

### 2.4 Web 取得

#### Mnemo の実装

**`tools/web_fetch.py`（1,074 行）**

- **取得:** httpx。リダイレクトの途中にも接続先の判定を当てる。
- **本文の取り出し:**
  - HTML を BeautifulSoup と readability で整理する。
  - markdownify で Markdown にする。
  - 広告・ナビゲーション・script を除く。
- **画像:**
  - 画像の一覧を取る（最大 50 件）。
  - 本文の画像を優先して並べる（ロゴ・アイコンなどは点数を下げる）。
  - `fetch_images` で実際にダウンロードする（既定 10 枚・最大 30 枚）。
- **文書の URL:** `markitdown_converter` に回す。
- **長さ:**
  - `max_length` / `offset` で分けて読む。
  - 上限は会話では 50,000 字、型（kata）の中では 500,000 字（師匠決定 2026-09-14）。

**wiseman `fetch.py`（187 行）**

- httpx と trafilatura で本文を取り出す。PDF は pdfminer.six。
- サイズと時間に上限がある。
- 同じ実行の中では同じ URL をキャッシュする（引用の offset とハッシュを安定させるため）。

**利用箇所:** 35 行。

#### OmniAISDK の現状

- **Web 取得の Capability が無い。**
- 仕様書に `web_fetch` が出てくるのは、他社のサーバー側ツールを比べた表の 1 行だけ。
- `DocumentParser` は `UrlRef` を**意図して受け付けない**（「勝手にダウンロードしない」`_doc_base.py:57`）。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| F1 | Capability が無い | `WebFetchCapability` を新しく作る。`fetch(url, *, max_length, offset, ...) -> FetchedPage(markdown, title, final_url, mime_type, images, links, truncated)`、同期と非同期の両方、extras は `[web]` | SDK | L |
| F2 | 取り出し方が 2 通りある | readability＋markdownify（本体）と trafilatura（wiseman）。SDK では選べるようにするか、1 つに寄せる。寄せると wiseman の引用照合（offset とハッシュ）が変わる | SDK と方針 | M |
| F3 | 接続先の判定の差し込み口 | リダイレクトの各段で呼ばれる `url_filter: Callable[[str], str \| None]`（SSRF 対策） | SDK | S |
| F4 | 画像の抽出とダウンロードの方針 | 一覧・点数づけ・上限を SDK に持たせる。点数づけを Mnemo に残すなら、抽出だけ SDK に置く | SDK と Mnemo | M |
| F5 | 文書の URL | 取ったバイト列を `DocumentParser` に渡す連結（取得は WebFetch、解析は Parser） | SDK | S |
| F6 | PDF の抽出器の違い | wiseman は pdfminer.six、SDK は pypdf。そろえるなら引用照合への影響を確かめる | 確認 | S |

---

### 2.5 文書解析

#### Mnemo の実装

**`core/markitdown_converter.py`（216 行）**

- Office・RTF は markitdown、PDF は pypdf。
- MIME と拡張子の表を持つ。**読める集合が経路によって違わないこと**を `tests/test_document_text.py` が固定している。
- 日本語の字形を正規化する（`normalize_cjk_forms`）。
- 変換したことの告知文（「Word 文書」などの呼び名）を付ける。

**利用箇所:** 24 行（添付、フルパスの読み込み、`web_fetch`、`document_processor`）。

**PDF の頁のつなぎ方に依存しているもの:**

- `pdf_split.py`（段落の分割）は、頁を `\n\n` でつなぐ形を前提にしている。
- 分割の規則の版（`PDF_SPLIT_VERSION`）を索引が持っている。

#### OmniAISDK の現状

- `MarkItDownDocumentParser`、`PyPDFDocumentParser`、`MimeRoutingDocumentParser`。
- **`DOCUMENT_EXTENSIONS` は Mnemo と同じ 8 種**（`.pdf .docx .doc .xlsx .xls .pptx .ppt .rtf`）。
- 返り値は `ParsedDocument(markdown, page_count, empty_reason)`。暗号化された PDF は空のパスワードで試す。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| D1 | 日本語の字形の正規化 | Mnemo の後処理として残す（SDK のオプションにしてもよい） | Mnemo | S |
| D2 | **出力が同じか** | PDF の頁のつなぎ方が `\n\n` か確かめる。違えば `PDF_SPLIT_VERSION` が上がり、PDF の索引を作り直すことになる。実際の添付で、本文の文字数・段落数の一致を数えて比べる | 確認 | S |
| D3 | 変換の告知文・MIME の呼び名 | Mnemo に残す | Mnemo | — |
| D4 | `markitdown` / `pypdf` への依存 | SDK の extras `[docs]` に移す | Mnemo | S |

---

### 2.6 音声認識

#### Mnemo の実装

**`core/transcribe.py`（405 行）**

- **モデル:** faster-whisper medium（CPU・int8）。置き場所は `~/.mnemo_recall/models/whisper-medium-ct2/` に固定。
- **流しながら処理する:**
  - ffmpeg の出力を 1.024 秒ずつ読み、VAD に流す。
  - メモリの使用量は素材の長さによらない（60 分の素材を全部持つと 230MB になる）。
- **発話区間:**
  - `mnemo_recall.vad.SpeechSegmenter`（Silero VAD）で切った区間だけを認識する（幻覚への対策 RC5）。
  - 末尾には無音を押し込んで、最後の区間を確定させる。
- **その他:**
  - 進捗を返す（ffmpeg の `Duration` から総時間を出す）。
  - `text` と `raw_text` を対で持つ。
  - 1 時間で打ち切る。

**利用箇所:** 2 行。

**`mnemo_recall/audio_monitor.py`（798 行）**

- リアルタイムの認識。OpenVINO の whisper-large-v3-turbo-int8 を GPU で動かす。
- マイクとスピーカーのループバックを別々に認識する。
- 既定デバイスが変わったら追いかける。

#### OmniAISDK の現状

- `FasterWhisperAudioAdapter`:
  - medium・CPU・int8。
  - デコードは PyAV（ffmpeg は要らない）。
  - faster-whisper 内蔵の `vad_filter=True` を使う。
  - `beam_size` を指定できる。
  - 結果は区間（segments）つき。
- **ファイルを丸ごと渡す形。** 進捗の通知も、逐次の出力も無い。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| T1 | 進捗・逐次の出力が無い | `transcribe_iter()`（区間を順に返す）か `on_progress` を足す。faster-whisper 自体は区間を生成器で返すので、それを外に出せばよい | SDK | S〜M |
| T2 | VAD の違い | Mnemo は自前の Silero の区間分け、SDK は faster-whisper の内蔵 VAD。**認識の結果が変わる**。実際の音声で比べ、どちらを使うか決める。Mnemo の VAD を残すなら、区間の音声を SDK に渡す形にする | 確認と方針 | M |
| T3 | モデルの置き場所 | 既存の ct2 ディレクトリを `model` にパスで渡せるか確かめる | 確認 | S |
| T4 | Recall（リアルタイム） | SDK にリアルタイムの認識は無い。**対象外を推奨** | 方針 | — |

---

### 2.7 音声合成

#### Mnemo の実装

- Mnemo 本体（`src/mnemo/`）に TTS は**無い**。
- コンパニオンの `companions/mnemo-slide-engine/mnemo_slide/media/tts.py`（563 行）が、外部の **EdgeTTS MCP サーバー**（`mcp_servers` に `name: "edgetts"` で登録したもの）を子プロセスとして起こし、MCP クライアントで呼ぶ。
- **使う機能:**
  - `edgetts_speech_synthesize(text, output="file", voice, rate, pitch)`
  - **記法**（強調・間・音量・話速・高さ）があるときは WAV、無いときは MP3 で返る。
  - 出力先は環境変数 `EDGETTS_MCP_DIR` で決める。
  - 差分だけ合成する（テキストと声の設定のハッシュを控える）。
- コンパニオンは自分のマニフェストで依存を宣言し、自分の環境に入れる（Mnemo の venv は使わない）。

#### OmniAISDK の現状

- `EdgeTTSAudioAdapter`:
  - 引数: `voice`、`rate`、`pitch`、`volume`。
  - **出力は MP3 だけ。** 読み上げの記法の解釈は無い。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| A1 | WAV の出力・読み上げの記法 | 記法を SSML などに変換して区間ごとに合成し、つないで WAV にする。EdgeTTS MCP サーバーと同じことを SDK に持たせる | SDK | M |
| A2 | コンパニオンの環境 | slide-engine のマニフェストに `omni-ai-sdk[audio]` を足す | コンパニオン | S |
| A3 | Mnemo のチャットが使う EdgeTTS MCP | ツールとしての MCP は残る（置き換えの対象外） | — | — |

---

### 2.8 画像生成

#### Mnemo の実装

**`tools/image_gen/`**

- **バックエンドの差し替え:** 宣言 `image_backends` でバックエンドを選ぶ（`registry.py`）。**LLM の設定とは独立**している（師匠決定 2026-09-02「完全に別パイプライン。ただし ELLM の認証情報は共有」）。
- **要求の形:** `ImageRequest` は縦横比と大きさを**言葉で**持つ（バックエンドごとに使える寸法が違うため）。
- **ELLM のバックエンド**（`backends/ellm.py` 255 行）:
  - lib の `agenerate_image` を呼ぶ。
  - 寸法は `SIZED_IMAGE_MODELS` のモデルにだけ渡す。
  - 日本語の説明に言い直す。
  - 時間の上限は 110 秒。

**利用箇所:** 11 行。

#### OmniAISDK の現状

- `ImageCapability` の抽象（`generate_image(prompt, model, size="1024x1024", n)`）と、型 `ImageResponse` / `GeneratedImage` だけがある。
- **実装は 0 本。**

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| I1 | 実装が無い | `EllmImageAdapter`（§2.12） | SDK（または別パッケージ） | S（C1 の上に載る） |
| I2 | 寸法の指定の形 | 言葉（縦横比・大きさ）から px への写しは Mnemo に残す。寸法が効かないモデルを知っているのはアダプターの役目 | SDK と Mnemo | S |
| I3 | ほかのバックエンド（ComfyUI など） | 今は無い。Mnemo の宣言の仕組みに残す | Mnemo | — |

---

### 2.9 MCP クライアント

#### Mnemo の実装（約 4,100 行）

- **`core/mcp_client.py`（1,434 行・`MCPClientManager`）:**
  - 複数のサーバーを同時に繋ぎ続ける。
  - 通信路: stdio、**SSE**、streamable-http。
  - 通知の受け取り、接続できたときの呼び戻し、接続前の門（`MCPConnectGuard`）。
  - 内部専用のツールを LLM から隠す。
  - 再接続。
- **`core/mcp_oauth.py`（616 行）:** OAuth（動的登録と事前発行のクライアント）。
- **`core/elicitation.py`（348 行）:** サーバーからの入力要求を人に取り次ぐ。
- **`bootstrap/mcp_bridge.py`（1,629 行）**
- **`core/mcp_call_wrappers.py`**
- `mcp_client` という文字列への言及は約 220 行。

#### OmniAISDK の現状

- `MCPClientAdapter`:
  - **1 接続だけ。** 通信路は stdio と streamable-http。
  - `headers`、時間の上限、標準エラー出力のログ。
  - `alist_tools` / `acall_tool`。結果は添付ファイルつき。
  - 開いたタスクと同じタスクの中で使う制約がある（anyio のキャンセルスコープ）。

#### 不足と必要な作業

| ID | 不足 | 必要な作業 | 置き場 | 大きさ |
|---|---|---|---|---|
| M1 | SSE の通信路 | 足す | SDK | S |
| M2 | OAuth | `auth: httpx.Auth` の差し込み口を足す。OAuth の手順そのものは Mnemo に残す | SDK と Mnemo | S |
| M3 | 入力要求（elicitation） | 呼び戻しの差し込み口を足す | SDK | S |
| M4 | サーバーからの通知 | 呼び戻しの差し込み口を足す | SDK | S |
| M5 | 複数サーバー・再接続・常駐 | タスクの制約のまま、背景で繋ぎ続ける Mnemo の使い方に合うか確かめる。合わなければマネージャーは Mnemo に残す | 方針 | L |
| M6 | 接続前の門・内部専用のツール | Mnemo の方針なので Mnemo に残す | Mnemo | — |

**推奨: MCP は置き換えの対象から外す。** あるいは最後の段で判断する。Mnemo の MCP 層は「繋ぎ続けて管理する」ことが本体で、SDK の「1 回開いて使う」部品とは役目が違う。

---

### 2.10 Rerank・Sandbox・Judge

- **Mnemo に対応する実装は見つからなかった。**
  - `rerank` の grep は 0 件。
  - コードの隔離実行は無い（`companion_platform/procs.py` はアプリの環境づくりで、別物）。
  - Judge は wiseman の中の段で判定しているが、SDK の Judge に当たる汎用の部品は見つけていない。
- 置き換える対象は無い。SDK の `FastEmbedRerankAdapter`（検索の並べ替え）と `DockerSandboxAdapter` は、**新しく使える機能**として記録しておく。

### 2.11 横断（設定・計測・切り替え）

| Mnemo | OmniAISDK | 推奨 |
|---|---|---|
| `config.json`＋`secrets.json`、プラグイン宣言、`core.enablement` | `OmniConfig`（TOML）、`build_client` | **Mnemo の設定を正にする。** アダプターはコードで組み立てる（SDK の TOML は使わない） |
| `ProviderProxy`（動かしたまま切り替える） | — | Mnemo に残す |
| `_CallMeter` | `InstrumentedChat`・`TelemetryTracker`・`pricing` | 任意。寄せれば費用の集計が得られる |
| ELLM 検索は「ほかに切り替えない」（E6） | `FallbackChatAdapter` | **使わない**（Mnemo の方針に反する） |
| provisioning（PONG の確認） | `cli checks` | Mnemo に残し、中で SDK を呼ぶ |

### 2.12 ELLM（3 つの領域にまたがる、いちばん大きい障害）

**Mnemo の使い方**

| 用途 | 呼び先 |
|---|---|
| Chat | `ellm_lib.py` |
| Web 検索 | `ellm_search.py` |
| 画像生成 | `image_gen/backends/ellm.py` |

- 3 つとも社内の `llmproviderlib`（m19 の公開 API）を呼ぶ。
- 認証は lib が解決する。順番はキャッシュ → ファイル（`~/.llmproviderlib`）→ HTTP での更新 → ブラウザでのログイン。
- Mnemo は呼び出し側からトークンを渡さない（渡すと、拒まれたトークンを lib が覚える処理が効かなくなる）。

**必要な作業**

1. 次の 3 本のアダプターを作る。いずれも lib を包むだけにする（lib を写さない。Mnemo も写しをやめて lib を呼ぶ形に直した経緯がある）。
   - `EllmChatAdapter`
   - `EllmSearchAdapter`
   - `EllmImageAdapter`
2. Chat 固有の手当てを持ち上げる。lib の打ち直しのときの `reset` イベント、壊れた封筒、`raw`/`parsed_blocks`。
3. Mnemo 固有のもの（ツールの規則・呼び名の目印・FSC-3・BR-1）は Mnemo の包みに残す。

**置き場の判断（師匠に決めてもらう）**

社内の lib に依存するものを SDK の本体に入れるかどうか。

- SDK の依存の規則は「コアは httpx と pydantic、ほかは extras」（ADR-02/03）。
- 社外へ出す可能性があるなら、**別パッケージ（例: `omni-ai-ellm`）にして、SDK の Capability を実装する**のを推奨する。

**確かめ方**

本物の ELLM でしか判定できない。師匠が席にいるときだけ実ジョブを入れられる。

---

## 3. 作業の一覧

| # | 作業 | 置き場 | 大きさ | 前提 |
|---|---|---|---|---|
| W1 | ELLM のアダプター（Chat・検索・画像） | SDK または別パッケージ | L | 置き場の決定 |
| W2 | ストリームしながらツールを回す（`ToolRunner.astream`） | SDK | M | — |
| W3 | スキーマ無しの JSON モードと寛容な取り出し | SDK | S | — |
| W4 | 無音の打ち切りと上流の冷却 | SDK | S〜M | — |
| W5 | `model_info`（文脈長）と `list_models` | SDK | M | — |
| W6 | `WebFetchCapability`（新しい機能・`[web]`・`url_filter`・画像） | SDK | L | 取り出し方の決定（F2） |
| W7 | 画像検索 | SDK | S〜M | — |
| W8 | 埋め込みを numpy で返す・トークナイザの共有 | SDK | S | — |
| W9 | 文字起こしの逐次出力と進捗 | SDK | S〜M | — |
| W10 | EdgeTTS の WAV と読み上げの記法 | SDK | M | — |
| W11 | MCP の SSE・auth・入力要求・通知の差し込み口 | SDK | M | 対象にするかの決定 |
| W12 | `OmniProvider(LLMProvider)` の包み（メッセージ・チャンク・エラーの変換、`describe_image`） | Mnemo | M | W2〜W5 |
| W13 | `builders.py` をプロバイダごとに差し替える（宣言・登録簿はそのまま） | Mnemo | S | W12 |
| W14 | `EmbeddingModel` の中身を SDK にする | Mnemo | S | W8・E3 の確認 |
| W15 | `web_search` / `ellm_search` / wiseman の検索の中身を SDK にする | Mnemo | S〜M | W1・W7 |
| W16 | `web_fetch` / wiseman の `fetch` の中身を SDK にする | Mnemo | M〜L | W6 |
| W17 | `markitdown_converter` の中身を SDK にする（字形の正規化は残す） | Mnemo | S | D2 の確認 |
| W18 | `transcribe` の中身を SDK にする（VAD の扱いを決める） | Mnemo | M | W9・T2 |
| W19 | `image_gen` の ELLM バックエンドを SDK にする | Mnemo | S | W1 |
| W20 | wiseman の `OllamaClient` を SDK にする | Mnemo | S | — |
| W21 | 依存を外す: `openai`・`google-genai`・`ddgs`・`markitdown`・`pypdf`・`pdfminer.six`・`trafilatura`・`readability-lxml`・`markdownify`・`beautifulsoup4` を SDK の extras に移す | Mnemo | S | 全段の後 |
| W22 | 試験: LLM の 43 ファイルを移植するか通るようにする。Mnemo の全試験。本物のキーでの実機試験。本物の ELLM での確認 | 両方 | M | 各段 |

大きさの目安: S は半日以内、M は 1〜2 日、L は 3 日以上。実測ではなく、読んだ量からの見積もり。

---

## 4. 進め方（推奨）

| 段 | 中身 | 理由 |
|---|---|---|
| 0 | §7 の判断をもらう | ELLM の置き場で W1 の形が変わる |
| 1 | **埋め込み・文書解析・DDG の文検索**（W8、W14、W17、W15 の DDG 部分） | SDK に同等のものがすでにある。数値と出力が同じか確かめれば差し替えられる。危険が小さい |
| 2 | **Chat の OpenAI 互換 5 種と Gemini**（W2〜W5、W12、W13、W20） | ELLM が無くても進められる。呼び出し側の 200 箇所は包みで守る |
| 3 | **ELLM**（W1、W15 の ELLM 部分、W19） | 本物の ELLM での確認が要る。師匠が席にいる時間に合わせる |
| 4 | **Web 取得・文字起こし・音声合成**（W6、W16、W9、W18、W10） | SDK への新機能が中心 |
| 5 | **MCP**（W11、M5）または対象外 | 役目が違うので最後に判断する |
| 6 | **依存の掃除**（W21） | 全部が置き換わってから |

各段で、本番（Mnemo の稼働中の版）に差し替え、ログで「その事象が起きたか」を数える。

---

## 5. 完了の判定のしかた

- **合成の試験は参考にとどめる。** 本物のキー・本物の ELLM・実際のデータで確かめる。
- **埋め込み:** 実際のターンと文書で、既存の npy との余弦を比べる（件数と最小値だけ残す）。
- **文書解析:** 実際の添付で、本文の文字数・段落数が一致するかを数える（本文は写さない）。
- **Web 取得・検索:** 実際の URL・クエリの一覧で、件数と本文の長さを比べる。
- **文字起こし:** 実際の音声で、文字数と区間の数を比べる。VAD の違いは聞いて比べるのではなく、差分の件数で見る。
- **Chat:** プロバイダごとに実機で、ストリーム・ツールのループ・JSON・画像の説明を通す。ELLM は本物で通す。
- **Mnemo の全試験**（5,000 件超）が通ること。

---

## 6. 危険と注意

1. **埋め込みの数値がずれると、全件の作り直しになる**（ターン・文書・重要度）。E3 を最初に確かめる。
2. **PDF のテキストの形が変わると、PDF の索引を作り直すことになり、wiseman の引用照合（offset とハッシュ）も壊れる。** D2・F6 で確かめる。
3. **実測で入れた手当てを置き換えのときに落とす危険。** 次のものを点検表にし、段ごとに残っているか確かめる。
   - §2.1 の Mnemo の手当て（冷却・無音の打ち切り・JSON の打ち直し・空の応答・上限での要約・画像の見せ直し・思考署名）
   - §2.2 のトークナイザの GIL への対策
4. **ELLM は確かめにくい。** 社内のサービスで、誤解を招くエラー文がある（必須項目が欠けると「Signature verification failed」が返る）。ブラウザでのログインが要る。確かめられるのは師匠が席にいる時だけ。
5. **社内の lib への依存を SDK の本体に入れると、SDK を社外へ出しにくくなる**（§2.12）。
6. **コンパニオンは別の環境で動く。** slide-engine の置き換えは、コンパニオンのマニフェストの変更になる。
7. **mu**（`vendor/mu`）は公式の `ollama` クライアントを直接 import している。この経路は詳しく調べていない（未調査）。

---

## 7. 師匠に決めてもらいたいこと（推奨つき）

1. **ELLM のアダプターの置き場** — 推奨: **SDK とは別のパッケージ**にし、SDK の Capability を実装する。社内の lib への依存を SDK の本体に入れないため。
2. **MCP クライアントを置き換えの対象にするか** — 推奨: **対象外**にする（Mnemo のマネージャーを残す）。SDK には差し込み口だけ足す。
3. **学術検索・ライブラリ検索・Recall（リアルタイムの認識）** — 推奨: **Mnemo に残す**（wiseman と recall の役目に密着しているため）。
4. **着手の順番** — 推奨: **第 1 段（埋め込み・文書解析・DDG の文検索）から始める**。
