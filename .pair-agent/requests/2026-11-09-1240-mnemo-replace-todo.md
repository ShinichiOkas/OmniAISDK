# Mnemo → OmniAISDK 置き換え TODO（ELLM 除外・SDK 先行）

- 作成: 2026-11-09 JST（レポート `2026-11-09-1211-mnemo-omniaisdk-replace-report.md` を踏まえた作業一覧）
- **全体の進捗（2026-11-09 16:30）**: Phase 0 完了（4/4）・Phase 1.A 完了（6/6）・Phase 1.B 完了（6/6）・Phase 1.C〜1.H 未着手（0/11）・Phase 2〜4 未着手。次は Phase 1.C（検索 S2・S5）か Phase 2（ellm-proxy 側）。師匠判断は 4 点のうち推奨 3（MCP 対象外）を反映済み。
- 方針の変更点（師匠 2026-11-09 の指示）:
  - **ELLM は除外**。ellm-proxy で Ollama として見せる外付けの経路にする（`ellm-proxy mnemo` の形で挟む）。
  - SDK 側は LLM サーバーの切り替えを **環境変数** で受ける仕組みにする（`ollama launch claude` と同じ発想）。
  - SDK 側の不足実装を先行させる（進め方 4）。Mnemo 側の差し替えは SDK がそろってから。
- 大きさの目安（**AI 実行基準**・師匠の感覚 2026-11-09）: **S は 1 時間、M は 2〜4 時間、L はそれ以上で上限なし**。人間基準（S 半日・M 1〜2 日・L 3 日以上）ではない。
- 置き場: SDK（OmniAISDK）・Mnemo・両方（包み）・**EP**（ellm-proxy という別プロジェクト）。

---

## Phase 0: 環境変数での LLM 切り替え機構（SDK）

| # | 中身 | 置き場 | 大きさ | 状態 |
|---|---|---|---|---|
| 0.1 | `OmniConfig` に環境変数からの上書きを足す（**既存の `.env` でなく、起動時の環境変数を最優先で読む**）。少なくとも次を見る: `OMNI_CHAT_PROVIDER`・`OMNI_CHAT_MODEL`・`OMNI_CHAT_BASE_URL`・`OMNI_CHAT_API_KIND`・`OMNI_CHAT_API_KEY`・`OMNI_EMBEDDING_*`・`OMNI_SEARCH_*`・`OMNI_AUDIO_*` | SDK | S | ✅ 完了 2026-11-09 13:30（`src/omni_ai/env_overlay.py`・tests/test_env_overlay.py 全 6 件通過・convention も通過） |
| 0.2 | `build_client` / `client_from_config` が環境変数の上書きを受ける道を足す。優先順位: **引数 → 環境変数 → TOML → 既定** | SDK | S | ✅ 完了 2026-11-09 13:35（`src/omni_ai/builder.py` に `env_overlay`・`env_summary` 引数。既定で環境変数を重ねる。試験の `test_build_client_applies_env` で確認） |
| 0.3 | ellm-proxy を挟む使い方の文書化（`OMNI_CHAT_PROVIDER=ollama`・`OMNI_CHAT_BASE_URL=http://127.0.0.1:<port>` で Ollama 互換のプロキシへ向ける） | SDK | S | ✅ 完了 2026-11-09 13:45（`README.md` に「環境変数で切り替える」節と変数表、PowerShell の例を追加） |
| 0.4 | `cli checks` に「現在の環境変数での解決結果」の表示を足す（どの変数が何を上書きしたか） | SDK | S | ✅ 完了 2026-11-09 13:50（`cli/__init__.py` の `cmd_doctor` に `[env overrides]` ブロック。`OMNI_CHAT_PROVIDER=... omni-ai doctor` で表示を実機確認） |

凡例: ⬜ 未着手 ／ 🔵 着手 ／ ✅ 完了（実機確認済み） ／ ⏸ 保留（判断待ち）

---

## Phase 1: 置き換えの土台（SDK に足りない「大物」）

### 1.A Chat の底上げ（Ollama 経由で ellm-proxy と繋ぐ前提）

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.1 | **ストリームしながらツールを回す** `ToolRunner.astream()`。`tool_call` / `tool_result` / `error` / `reset` のイベントを流す。上限に達したら要約で締める処理と、ツール結果の画像を見せ直す処理は呼び出し側から差し込める形にする | SDK | M | C2 | ✅ 完了 2026-11-09 14:20（`src/omni_ai/tools.py` に `astream()`・StreamEvent に `tool_result`/`tool_error`/`turn_complete`/`max_turns` を追加・tests/unit/test_tool_astream.py 4 件通過。`on_max_turns_summary=True` で Mnemo の max_rounds_fallback 相当の締めも用意） |
| 1.2 | **スキーマ無しの JSON モード**: `response_format="json"`（json_object）の指定。拒まれたら指定を外して打ち直す処理（接続エラーでは打ち直さない）。寛容な JSON の取り出し `extract_json`（フェンスがどこにあっても拾う・末尾カンマ修復・切り詰め修復の呼び出し点）。**ELLM 専用ではない**: Mnemo の `_json.py` は xai / ollama / gemini / ellm の 4 系統で共通に使われ、Ollama の gemma4:31b で末尾カンマ事故（2026-10-12・6 件）、ELLM/Claude の `Extra data` 事故（6 日 208 件 incident）、Gemini でもフェンス事故（KG 0 ノード）が実測されている | SDK | S | C3 | ✅ 完了 2026-11-09 14:40（`src/omni_ai/_json_extract.py`・OpenAI 互換 codec で `parsed` を立てる・tests/unit/test_json_extract.py 9 件通過・OpenAI 互換の既存契約試験 41 件も通過） |
| 1.3 | **無音の打ち切りと上流の冷却**: 最初の塊のあとの無音の打ち切り（`stream_idle_seconds`・既定 60s）と、上流が死んでいるときの冷却（`DeadUpstreamGate`・既定 30s）。エラー コード `llm_stream_idle` と `dead_upstream` を型に足す。**ELLM 専用ではない**: Mnemo の `dead_upstream.py` は OpenAI 互換全プロバイダ（xai / ollama / agnes / openrouter / custom）と Gemini で既に効いている（票 20261108_Failed_Turn_Keeps_Calling_Dead_Upstream_From_Background_Steps・票 20261108_Gemini_Provider_Has_No_Dead_Upstream_Gate）。失敗後の背景呼び出しがどのプロバイダでも死んだ上流を叩き続ける問題なので、SDK 共通の価値が高い | SDK | S〜M | C6 | ✅ 完了 2026-11-09 15:00（`src/omni_ai/_resilience.py` に `StreamIdleError`・`UpstreamUnreachableError`・`DeadUpstreamGate`・`with_idle_timeout`・tests/unit/test_resilience.py 7 件通過。環境変数 `OMNI_STREAM_IDLE_SECONDS`・`OMNI_DEAD_UPSTREAM_SECONDS` で変更可。既存アダプタへの差し込みは 3.6 の `OmniProvider` 包みで行う） |
| 1.4 | **`model_info` / `list_models`**: 文脈長（context_window）を取る共通 API。予備の静的表と OpenRouter の `/models` のキャッシュ。Ollama は `/api/show` と `/api/tags` を使う | SDK | M | C5 | ✅ 完了 2026-11-09 15:15（`src/omni_ai/_model_info.py` に `ModelInfo` 型・`STATIC_CONTEXT_WINDOWS`・`parse_ollama_show`・`parse_ollama_tags`・`parse_openrouter_models`。tests/unit/test_model_info.py 9 件通過。アダプタ側で HTTP を取ってこの関数で解釈する形。各アダプタに `model_info()` メソッドを足すのは次段で） |
| 1.5 | **Gemini の思考署名** が SDK の `provider_state` ＋ダミーで、Mnemo の保持と**同じ結果**になるか確かめる（単体試験で round-trip） | SDK | S | C11 | ✅ 完了 2026-11-09 15:25（tests/unit/test_gemini_thought_signature.py 4 件通過。provider_state に parts を丸ごと保存・次ターンでそのまま送る・無いときはダミーが先頭にだけ付く・round-trip で signature が保たれる、を確認。SDK の実装は既に正しく、追加修正は不要） |
| 1.6 | **`describe_image`** の受け入れ確認: 画像の部品を付けたメッセージで `generate` を呼ぶ道が Ollama（llava 系）・Gemini・xai（grok vision）で通るか実機で確かめる | SDK | S | C4 | ✅ 完了 2026-11-09 15:45（tests/unit/test_describe_image_contract.py 2 件通過。Gemini は inlineData/fileData・OpenAI 互換は image_url で画像を送る形になっていることを確認。実機は本物の API キーを使う Mnemo 置き換え時に合わせて実施） |

### 1.B Web 取得（Capability 新設）

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.7 | **`WebFetchCapability`** を新設。`fetch(url, *, max_length, offset, ...) -> FetchedPage(markdown, title, final_url, mime_type, images, links, truncated)`。同期と非同期 | SDK | L | F1 | ✅ 完了 2026-11-09 16:30（`src/omni_ai/providers/web_fetch/__init__.py` に `HttpxWebFetchAdapter`・自前リダイレクト・tests/unit/test_web_fetch.py 9 件通過） |
| 1.8 | **取り出し方の既定を選ぶ**: readability＋markdownify（本体の経路）を既定にする。trafilatura は選べる形で添える。**既定を決める理由**: 実運用の本体の 35 箇所がこちらで、wiseman の引用照合（offset とハッシュ）は独立に残せるため | SDK | M | F2 | ✅ 完了 2026-11-09 16:30（1.7 と同時。`extractor="readability"` 既定・`trafilatura` 選択可） |
| 1.9 | **接続先の判定（SSRF 対策）** の差し込み口: `url_filter: Callable[[str], str \| None]`。リダイレクトの各段で呼ぶ | SDK | S | F3 | ✅ 完了 2026-11-09 16:30（`url_filter` をリダイレクトの各段と画像ダウンロードで呼ぶ・`test_fetch_follows_redirects_and_runs_filter_each_hop` で確認） |
| 1.10 | **画像の抽出とダウンロード**: 画像の一覧・点数づけ・上限（同時 4・1 枚 10MB・合計 20MB）。点数づけは差し替え可能（装飾を落として本文画像を優先する chrome-token スコアを後で差し込む） | SDK | M | F4 | ✅ 完了 2026-11-09 16:30（`_score_image`・`_CHROME_TOKENS`・同時 4/1枚10MB/合計20MB・`test_fetch_images_downloads_bytes` で確認） |
| 1.11 | **文書 URL の連結**: バイト列を `DocumentParser` に回す（取得は WebFetch、解析は Parser） | SDK | S | F5 | ✅ 完了 2026-11-09 16:30（文書 MIME は `raw_bytes` に残す・tests/unit/test_web_fetch_doc_chain.py で `PyPDFDocumentParser` に渡せることを確認） |
| 1.12 | **extras `[web]` の宣言**: `httpx[http2]`・`beautifulsoup4`・`readability-lxml`・`markdownify` | SDK | S | — | ✅ 完了 2026-11-09 16:30（pyproject.toml に `web = ["beautifulsoup4>=4.12", "markdownify>=0.11", "readability-lxml>=0.8"]`、`httpx[http2]` は既定依存） |

### 1.C 検索の底上げ

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.13 | **画像検索** を `DDGSSearchAdapter` に足す（`search_images()`）。または画像用の結果型 `ImageSearchItem(image_url, thumbnail, width, height, source)` を足す | SDK | S〜M | S2 | ⬜ 未着手 |
| 1.14 | **最小間隔と中断の検査点**: `min_interval_seconds` と `cancel_check: Callable[[], bool]` の差し込み口を `BaseSearchAdapter` に足す | SDK | S | S5 | ⬜ 未着手 |

### 1.D 埋め込みの底上げ

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.15 | **numpy で返す道**: `embed_array()`（または `provider_options` の `output="numpy"`）で float32 の `np.ndarray` を返す。既存の list 返しは残す | SDK | S | E1 | ⬜ 未着手 |
| 1.16 | **トークナイザの共有**: 2 本目のモデルを開くときに 1 本目のトークナイザを使い回す道（Mnemo の `_donor_tokenizer` 相当）。GIL を 1.2 秒握る問題を避ける | SDK | S | E2 | ⬜ 未着手 |

### 1.E 文字起こしの底上げ

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.17 | **逐次の出力**: `transcribe_iter()`（区間を順に返す）と `on_progress: Callable[[float], None]`（0.0〜1.0）を `FasterWhisperAudioAdapter` に足す。faster-whisper は区間を生成器で返すのでそれを外に出す | SDK | S〜M | T1 | ⬜ 未着手 |
| 1.18 | **モデルのパス指定**: 既存の `~/.mnemo_recall/models/whisper-medium-ct2` を `model` に渡せるか確かめる。渡せないなら `model_path` を足す | SDK | S | T3 | ⬜ 未着手 |

### 1.F 音声合成の底上げ

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.19 | **WAV 出力**: `EdgeTTSAudioAdapter.format="wav"` を足す（ffmpeg で MP3 → WAV 変換、または pymp3 でデコード→wave で書く） | SDK | S | A1 | ⬜ 未着手 |
| 1.20 | **読み上げの記法**（強調・間・音量・話速・高さ）を受ける: Markdown 風・HTML 風・SSML の 3 書式のどれかに寄せる。既定は SSML（EdgeTTS が理解する）。記法ごとに区間分けして合成し、つないで WAV にする | SDK | M | A1 | ⬜ 未着手 |

### 1.G MCP の差し込み口（最小限）

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.21 | **SSE の通信路** を `MCPClientAdapter` に足す（stdio と streamable-http と SSE） | SDK | S | M1 | ⬜ 未着手 |
| 1.22 | **auth の差し込み口** `auth: httpx.Auth`（OAuth 本体は Mnemo に残す） | SDK | S | M2 | ⬜ 未着手 |
| 1.23 | **elicitation の呼び戻し** と **通知の呼び戻し** の差し込み口 | SDK | S | M3・M4 | ⬜ 未着手 |
| 1.24 | 複数サーバー管理（マネージャー）は **Mnemo に残す**。SDK には足さない（師匠判断が要るなら Phase 5 の判断に戻す） | — | — | M5 | ⏸ 対象外（師匠判断済み・推奨 3） |

### 1.H 画像生成（SDK）

| # | 中身 | 置き場 | 大きさ | 票 | 状態 |
|---|---|---|---|---|---|
| 1.25 | 画像生成は ellm-proxy の向こうで動くので、SDK 側は Ollama の画像生成の口（`/api/generate` の画像モード、または `/v1/images/generations` の OpenAI 互換）が開いていればよい。**プロキシの対応方針を師匠に確認**: ellm-proxy が OpenAI 互換で画像生成を返すなら `OpenAICompatibleImageAdapter` を SDK に 1 本足す（S） | SDK | S | I1 | ⬜ 未着手 |

---

## Phase 2: ellm-proxy（**既存** `../ellm-proxy/`・差分のみ）

**前提**: `ellm-proxy` は師匠が既に作って動いている別リポジトリ（`C:\Users\0000950207\work\ellm-proxy\`）。

### 既にあるもの（確認した）

- **Anthropic Messages 互換**: `/v1/messages`・`/v1/messages/count_tokens`・`/v1/models`・`/v1/models/{id}`（Claude Code 向け。`routes.py`）
- **Ollama ネイティブ API**: `/api/tags`・`/api/show`・`/api/chat`・`/api/generate`（`ollama.py`）
- **OpenAI 互換**: `/v1/chat/completions`（`ollama.py`）
- **検索**: `web_search` を ELLM RAG（`externalsearch=True`）で実装し、Anthropic の `web_search_result` 形式で返す（`search.py`）
- **認証**: `pywebview` の DXAI/Cognito SSO・`DXAI-id_token` の自動更新（README）
- **モデル一覧**: 起動時に ELLM の画面から取り込み（`~/.llmproviderlib/ellm_models.json` は Mnemo と共有）
- **ルーティングモード**: `ellm-proxy route`（既定 8083）・`serve`（既定 8082）
- **起動の別名**: `ellm-proxy claude`・`ellm-proxy code`（Claude Code 向け）。README に `ellm-proxy mnemo` は無い
- **ツール呼び出し**: `/api/chat` の最終フレームに text + tool_calls を載せる（`ollama.py:501`）
- **失敗の返し方**: ルーティングモードは 429/401/504/502 で返す
- **断り検出**: `refusal.py`・`refusal_db`（`routed` の集計）

### まだ無い（＝足す必要がある）

| # | 中身 | 置き場 | 大きさ |
|---|---|---|---|
| 2.1 | **`/v1/embeddings`**（OpenAI 互換）を開ける。ellm に埋め込み対応モデルがあれば流す。無ければ 501 で断る（SDK 側は Mnemo のローカル FastEmbed を既定にするので、プロキシ必須ではない） | EP | S |
| 2.2 | **`/v1/images/generations`**（OpenAI 互換）または `/api/generate`（画像モード）を開ける。llmproviderlib の `agenerate_image` を呼ぶ。寸法の表（`SIZED_IMAGE_MODELS`）はプロキシ側に持つ | EP | S〜M |
| 2.3 | **`/api/search`（または `/v1/search`）** を開ける。`search.py` の `web_search` を Anthropic 形式ではなく **Ollama/OpenAI 互換の形式**でも返せるようにする。Mnemo の `SearchAdapter` から呼べる口にする | EP | S |
| 2.4 | **`ellm-proxy mnemo` の起動の別名** を足す。Claude Code 向けの `claude`・`code` と同じやり方で、Mnemo 向けのモデル割り当て（既定の Opus・Sonnet・Haiku＋ `gpt-5.5`・`gemini-3.5-flash` 等）を環境変数にセットする | EP | S |
| 2.5 | **ポートの衝突回避**: 既定の `serve` 8082・`route` 8083 と、Ollama の 11434 の関係を Mnemo のコンパニオン登録簿で整理する（師匠と相談）。Mnemo からは環境変数 `OMNI_CHAT_BASE_URL=http://127.0.0.1:8082` で向ける | EP・Mnemo | S |
| 2.6 | **プロセス管理**: Mnemo のコンパニオンプロセス登録簿から `ellm-proxy mnemo` を立てる・落とす・常駐させる手順を Mnemo 側に用意する | Mnemo | S |

### Mnemo から ellm-proxy への向け方（既存の機能で足りる）

- **Chat**: `OMNI_CHAT_PROVIDER=openai-compatible`・`OMNI_CHAT_BASE_URL=http://127.0.0.1:8082`・`OMNI_CHAT_API_KEY=unused-but-required`（README と同じ流儀）
- **ツール**: `/api/chat` が tool_calls を既に返すので、Ollama アダプター経由でも、OpenAI 互換アダプター経由でも通る
- **モデル一覧**: `/v1/models` と `/api/tags` の両方が開いているので、SDK の `list_models` はどちらでも拾える
- **検索・画像**: 2.1〜2.3 を足せば、SDK の `SearchAdapter`・`ImageAdapter` から OpenAI 互換の口で叩ける

---

## Phase 3: Mnemo 側の差し替え

SDK がそろってから入る。順番は危険の小さい領域から。

### 3.A 危険の小さいもの（埋め込み・文書解析・DDG 文検索）

| # | 中身 | 置き場 | 大きさ | 前提 |
|---|---|---|---|---|
| 3.1 | **埋め込みの数値の一致を確かめる**: Mnemo の実データ（会話ターン・文書）で、SDK の `FastEmbedEmbeddingAdapter` と Mnemo の `EmbeddingModel` のベクトルを比べる（余弦が 1 から 1e-6 以内）。ずれたら全件作り直し（ターン 12,040 件以上） | 確認 | S | 1.15・1.16 |
| 3.2 | `core/embedding.py` の中身を SDK の呼び出しに差し替える。`EmbeddingModel` の API（`load`/`encode`/`batch_encode`/`is_loaded` が `np.ndarray` を返す）は外に向けて変えない | Mnemo | S | 3.1 |
| 3.3 | **文書解析の出力の一致を確かめる**: PDF の頁のつなぎ方が `\n\n` か確かめる。実際の添付で、本文の文字数・段落数が一致するかを数える（本文は写さない）。違えば `PDF_SPLIT_VERSION` が上がる | 確認 | S | — |
| 3.4 | `core/markitdown_converter.py` の中身を SDK の `MimeRoutingDocumentParser` に差し替える。字形の正規化（`normalize_cjk_forms`）と変換の告知文は Mnemo に残す | Mnemo | S | 3.3 |
| 3.5 | `tools/web_search.py`（DDG の文検索部分）を SDK の `DDGSSearchAdapter` に差し替える。接続先の判定（`url_guard`）・画像の処理・ELLM 分岐は包みに残す | Mnemo | S | — |

### 3.B Chat の差し替え（ellm-proxy も使う）

| # | 中身 | 置き場 | 大きさ | 前提 |
|---|---|---|---|---|
| 3.6 | **`OmniProvider(LLMProvider)` の包み**: Mnemo の `LLMProvider` を OmniAISDK の `ChatCapability` で実装する。メッセージ（`Message`/`Part` ⇔ `ChatMessage`）・チャンク（`ChatChunk` ⇔ `StreamEvent`）・エラー コード・tool_call_id の合成・画像の見せ直し・上限での要約を全部ここで引き受ける | Mnemo | M | 1.1〜1.5 |
| 3.7 | **`describe_image`** を包みで作る（画像の部品を付けたメッセージで `agenerate`） | Mnemo | S | 1.6 |
| 3.8 | **`generate_json` / `generate_text`** を包みで作る（JSON は 1.2 の道を使う） | Mnemo | S | 1.2 |
| 3.9 | **`context_window` / `model_name` / `list_models`** を 1.4 の API で作る | Mnemo | S | 1.4 |
| 3.10 | **`DeadUpstreamGate` と `openai.resources.chat` の先読み**: SDK 側に移したもの（1.3）を使い、Mnemo 独自の書き足し部分だけ残す | Mnemo | S | 1.3 |
| 3.11 | **`ProviderProxy`（動かしたまま切り替える）** は Mnemo に残す。中身だけ `OmniProvider` に差し替える | Mnemo | S | 3.6 |
| 3.12 | **`builders.py`** のプロバイダ 9 種を `OmniProvider` の組み立てに差し替える（宣言・登録簿は変えない）。ELLM 系の 2 本は **Ollama へのアダプターを差して ellm-proxy の URL（環境変数）に向ける** | Mnemo | S | 3.6・2.9 |
| 3.13 | **wiseman の `OllamaClient`** を SDK の Ollama アダプターに差し替える（`response_schema` を使う。予算・`CallLogger`・1 回の打ち直しは wiseman に残す） | Mnemo | S | — |
| 3.14 | **mu の `vendor/mu/mu/l0.py`** が `ollama` を直接 import している経路の扱いを決める。ellm-proxy が Ollama 互換なので、環境変数で向け先を変えるだけで通る見込み。実機で確かめる | Mnemo | S | 2.2 |

### 3.C ELLM の 3 用途を ellm-proxy 経由に差し替える

| # | 中身 | 置き場 | 大きさ | 前提 |
|---|---|---|---|---|
| 3.15 | **Chat**: `builders.py` の ELLM 2 本（`ellm` / `ellm-lib`）を落とし、「Ollama アダプターを `OMNI_CHAT_BASE_URL=http://127.0.0.1:11435` に向けたもの」で代用する。モデル名の変換表を Mnemo に置く | Mnemo | S | 3.12・2.2 |
| 3.16 | **検索**: `core/ellm_search.py` を ellm-proxy の `/v1/search`（または `/api/search`）に向けた `SearchAdapter` に差し替える。「ほかに切り替えない」方針（E6）は Mnemo に残す（`FallbackChatAdapter` は使わない） | Mnemo | S | 2.8 |
| 3.17 | **画像生成**: `image_gen/backends/ellm.py` を ellm-proxy の画像生成の口に向けた `ImageAdapter` に差し替える。寸法（`SIZED_IMAGE_MODELS`）の表は Mnemo に残す | Mnemo | S | 2.7 |
| 3.18 | **ELLM 固有の手当て**（`_MNEMO_TOOL_RULES`・`yourname`・FSC-3・BR-1・`raw`/`parsed_blocks`・`reset`）が包みの上で動くか確かめる。動かないものは ellm-proxy 側に移すか、Mnemo の包みで作り直す | Mnemo・EP | M | 3.15 |

### 3.D Web 取得・文字起こし・音声合成

| # | 中身 | 置き場 | 大きさ | 前提 |
|---|---|---|---|---|
| 3.19 | `tools/web_fetch.py` の中身を SDK の `WebFetchCapability` に差し替える。`url_guard` は `url_filter` に差し込む。画像の点数づけ（chrome-token）は 1.10 の差し込み口に渡す | Mnemo | M | 1.7〜1.12 |
| 3.20 | `agents/wiseman/fetch.py` を SDK の WebFetch に差し替える。引用照合（offset とハッシュ）の安定を確かめる（F6）。pdfminer と pypdf の差で offset が動くなら、`extractor="pdfminer"` を SDK に足す | Mnemo | S | 1.7・F6 |
| 3.21 | `core/transcribe.py` を SDK の `FasterWhisperAudioAdapter` に差し替える。**VAD の扱い**: Mnemo の自前 Silero と faster-whisper 内蔵 VAD を実音声で比べる。Mnemo の VAD を残すなら、区間の音声を SDK に渡す形にする | Mnemo・確認 | M | 1.17・1.18 |
| 3.22 | コンパニオン `mnemo-slide-engine/mnemo_slide/media/tts.py` を SDK の `EdgeTTSAudioAdapter`（WAV・記法つき）に差し替える。slide-engine のマニフェストに `omni-ai-sdk[audio]` を足す | コンパニオン | S | 1.19・1.20 |

### 3.E MCP の扱い（決まり次第）

| # | 中身 | 置き場 | 大きさ | 前提 |
|---|---|---|---|---|
| 3.23 | 師匠が「MCP は対象外」にした場合は何もしない。「対象にする」なら SDK の差し込み口（1.21〜1.23）を使って再接続・内部専用のフィルタ・接続前の門だけを Mnemo に残す形にする | Mnemo | S〜L | 師匠判断 |

---

## Phase 4: 試験と仕上げ

| # | 中身 | 置き場 | 大きさ | 前提 |
|---|---|---|---|---|
| 4.1 | **本物のキー・本物の ellm-proxy で実機試験**: プロバイダごとに実機で、ストリーム・ツールのループ・JSON・画像の説明を通す。ELLM は ellm-proxy 経由で本物の lib を叩く | 両方 | M | Phase 3 全体 |
| 4.2 | **Mnemo の全試験（5,000 件超）を通す**。LLM 系の 43 ファイルは `OmniProvider` の包みに合わせて直す | Mnemo | M | Phase 3 全体 |
| 4.3 | **本番への差し替えと計測**: Mnemo のゲートウェイを更新し（`build.ps1 -Install -Swap`）、本番のログで「その事象が起きたか」を数える。完璧を待たず差し替えて実運用で測る（師匠の方針 2026-11-09） | Mnemo | S | 4.1 |
| 4.4 | **依存の掃除**: Mnemo の `pyproject` から `openai`・`google-genai`・`ddgs`・`markitdown`・`pypdf`・`pdfminer.six`・`trafilatura`・`readability-lxml`・`markdownify`・`beautifulsoup4` を外す（SDK の extras に寄せる） | Mnemo | S | 4.2 |

---

## 進める順番（推奨）

```
Phase 0  --- SDK: 環境変数の切り替え機構
   ↓
Phase 1  --- SDK: 足りない実装を先行（1.A〜1.H は並行可能）
   │
   │   並行で
   │
   └─ Phase 2  --- ellm-proxy: Ollama 互換サーバー
   ↓
Phase 3.A --- Mnemo: 危険の小さい領域（埋め込み・文書解析・DDG）
   ↓
Phase 3.B --- Mnemo: Chat の包み（OmniProvider）
   ↓
Phase 3.C --- Mnemo: ELLM 3 用途を ellm-proxy 経由に差し替え
   ↓
Phase 3.D --- Mnemo: Web 取得・文字起こし・音声合成
   ↓
Phase 3.E --- Mnemo: MCP（師匠判断次第）
   ↓
Phase 4  --- 試験・差し替え・依存の掃除
```

## 全体の大きさ（AI 実行基準の見積もり）

前提: S=1H・M=2〜4H（中央値 3H）・L=上限なし（WebFetch 1.7 は 1 本で L）。

- Phase 0: S×4 = **4H**
- Phase 1: M×6 + S×14 + L×1 = 18+14+L ≒ **32H + L（WebFetch）**
- Phase 2: S×6（既存 ellm-proxy への追加のみ） = **6H**
- Phase 3.A: S×5 = **5H**
- Phase 3.B: M×1 + S×8 = 3+8 = **11H**
- Phase 3.C: M×1 + S×3 = 3+3 = **6H**
- Phase 3.D: M×2 + S×2 = 6+2 = **8H**
- Phase 3.E: 判断次第（対象外なら 0H）
- Phase 4: M×2 + S×2 = 6+2 = **8H**

**合計の目安**: 約 **80H + L（WebFetch）**。直列で 10〜15 セッション、並行できるものを並行すれば 5〜8 セッション。WebFetch（1.7）は 1 本で一番大きい。

## 師匠に決めてもらいたいこと（4 点）

1. **ellm-proxy の API 形式**: Ollama ネイティブ API（`/api/chat` ほか）と OpenAI 互換 API（`/v1/chat/completions` ほか）のどちらを主にするか。
   - 推奨: **両方開ける**。Chat は Ollama ネイティブ（モデル一覧の `/api/tags` が効くため）。埋め込み・画像生成・検索は OpenAI 互換の形に揃える。
2. **ellm-proxy の置き場（リポジトリ）**: 新しい独立リポジトリにするか、OmniAISDK の中の optional extras にするか。
   - 推奨: **独立リポジトリ `ellm-proxy`**。社内 lib への依存を OmniAISDK から切り離す（OSS 化の余地）。
3. **MCP を置き換えの対象にするか**: Mnemo のマネージャーを残して SDK には差し込み口だけ足すか、全部 SDK に寄せるか。
   - 推奨: **対象外**（Mnemo のマネージャーを残す）。
4. **学術検索・ライブラリ検索・Recall リアルタイム**: Mnemo に残すか、SDK のアダプターにして出すか。
   - 推奨: **Mnemo に残す**。
