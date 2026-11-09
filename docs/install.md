# OmniAI SDK インストールと設定

## 1. インストール

Python 3.11 以上が必要です（Windows / macOS / Linux）。

```bash
pip install -e .                 # コア（Gemini / Ollama の Chat・埋め込み・判定など）
omni-ai                          # メニュー方式のセットアップ（以下の 2〜5 を案内します）
```

追加パッケージ（extras）は、使う機能に合わせて入れます。メニューの「4) 追加パッケージの導入」からも入れられます。

| extras | 入るもの | 使う機能 | 備考 |
| :--- | :--- | :--- | :--- |
| `local` | fastembed（ONNX） | 埋め込み・再順位付けをプロセス内で | 初回にモデルを取得（埋め込み 約 220MB、再順位付け 約 570MB） |
| `search` | ddgs | Web 検索（キー不要） | 検索語は外部の検索エンジンに送られる |
| `docs` | pypdf・markitdown | 文書解析（PDF・Office・RTF） | |
| `mcp` | mcp SDK 2.x | MCP サーバーのツール | |
| `audio` | edge-tts・faster-whisper | 音声合成・音声認識 | 合成の文章は Microsoft に送られる。認識のモデルは medium で約 1.5GB |

```bash
pip install -e ".[local,search,docs,mcp,audio]"
```

## 2. 外部の前提

SDK は Python 側のものだけを入れます。次のものは、必要に応じてご自身で入れてください（`omni-ai doctor` が足りないものを教えます）。

| もの | 必要な機能 | 条件 |
| :--- | :--- | :--- |
| [Ollama](https://ollama.com/download) | Ollama の Chat・埋め込み・判定 | 判定（decision モデル）は **0.35.0 以上** |
| [Docker](https://docs.docker.com/get-started/get-docker/) | コード実行（Sandbox） | Docker Desktop は **Linux コンテナのモード**。Linux では利用者を `docker` グループに入れる |
| Gemini の API キー | Gemini の Chat・埋め込み | [Google AI Studio](https://aistudio.google.com/) で発行 |
| xAI / Agnes / OpenRouter / Ollama Cloud の API キー | それぞれの Chat | 各社の画面で発行（環境変数は `XAI_API_KEY` / `AGNES_API_KEY` / `OPENROUTER_API_KEY` / `OLLAMA_API_KEY`） |
| OpenAI 互換サーバー（llama.cpp の `llama-server` など） | `openai-compatible` の Chat | 自分で起動しておく。接続先の URL は必須（既定値なし）、キーは任意 |

## 3. 設定ファイル

OS 標準の設定ディレクトリに置きます（`omni-ai config path` で場所を表示。環境変数 `OMNI_CONFIG_DIR` で変更できます）。

- `config.toml` … 接続先と機能の構成。人に見せたり共有したりしてよい
- `secrets.toml` … API キー。macOS / Linux では本人だけが読める権限（600）で保存される

```toml
# config.toml
[ollama]
base_url = "http://127.0.0.1:11434"

[client.chat]
providers = ["gemini", "ollama"]          # 先頭から使い、失敗したら次へ（フォールバック）
models = { gemini = "gemini-3.1-flash-lite", ollama = "gemma4:31b-cloud" }

[client.embedding]
provider = "fastembed"                    # gemini / ollama / fastembed

[client.rerank]
provider = "fastembed"

[client.search]
provider = "ddgs"
region = "ja-jp"

[client.doc_parser]
provider = "default"                      # PDF は pypdf、Office は markitdown

[client.judge]
provider = "ollama"
model = "tev1:0.8b"

[client.audio]
voice = "ja-JP-NanamiNeural"
whisper_model = "medium"

[client.sandbox]
provider = "docker"
```

Chat のプロバイダーは `gemini` / `ollama` / `ollama-cloud` / `xai` / `agnes` / `openrouter` / `openai-compatible` から選べます。Gemini 以外は既定のモデルが無いので、`models` に書いてください。

```toml
# config.toml（Chat をクラウドと手元のサーバーで組む例）
[openai-compatible]
base_url = "http://127.0.0.1:8080"        # /v1 は付けても付けなくてもよい

[client.chat]
providers = ["openrouter", "xai", "openai-compatible"]
models = { openrouter = "openai/gpt-4o-mini", xai = "grok-4", openai-compatible = "local" }
```

```toml
# secrets.toml（共有しない）
[gemini]
api_key = "..."

[openrouter]
api_key = "..."
```

API キーや接続先は、次の順で探します: 引数 → `OMNI_<PROVIDER>_<KEY>`（名前のハイフンは `_`。例: `OMNI_OLLAMA_CLOUD_API_KEY`）→ 各社の環境変数（`GEMINI_API_KEY` など）→ `secrets.toml` → `config.toml`。

```python
from omni_ai import OmniClient

ai = OmniClient.from_config()     # 誤りがあれば ConfigurationError（場所つきの一覧）
```

## 4. コマンド

| コマンド | 内容 |
| :--- | :--- |
| `omni-ai` | メニュー方式のセットアップ |
| `omni-ai doctor` | 環境と設定の診断（足りないものと直し方） |
| `omni-ai config path / show / validate` | 設定の場所・内容・検証 |
| `omni-ai models catalog / list / size / pull / remove` | モデルの一覧・容量の確認・取得・削除（取得の前に容量を表示して確認） |

モデルの名前は `fastembed-embedding`・`fastembed-rerank`・`whisper-medium`・`ollama:<モデル>`・`docker:<イメージ>` です。

## 5. Windows での注意

### PowerShell で設定ファイルを直書きする場合

`Set-Content -Encoding UTF8` は **BOM 付き** UTF-8 を書き出します。tomllib は BOM 付きファイルを `Invalid statement (at line 1, column 1)` で拒否するので、**BOM 無し**で書く必要があります:

```powershell
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($path, $content, $utf8NoBom)
```

または `omni-ai setup` を使えばこの問題はありません。

### API キーの環境変数

現在のセッションのみ:

```powershell
$env:GEMINI_API_KEY = 'sk-...'
```

恒久的に（ユーザー環境変数、新しいシェルで有効）:

```powershell
[Environment]::SetEnvironmentVariable('GEMINI_API_KEY', 'sk-...', 'User')
```

### `omni-ai` が見つからないとき

`py -m pip install -e .` で入れた直後に `omni-ai` が PATH にないときは、Python の `Scripts\` ディレクトリを PATH に追加してください（通常は `%LOCALAPPDATA%\Programs\Python\Python312\Scripts`）。

### Python 3.14 の `httpx[http2]`

`httpx[http2]` の wheel が Python 3.14 で未提供のことがあります。安定のため **Python 3.12** を推奨します（`py -3.12 -m pip install -e .`）。
