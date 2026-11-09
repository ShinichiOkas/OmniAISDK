# OmniAI SDK

OmniAI SDK は、複数の AI プロバイダとローカル実行環境を統合し、生成AI・RAG・マルチモーダル処理・ツール連携を一貫した Python インターフェースで扱えるための SDK の設計・実装方針をまとめたリポジトリです。

このリポジトリでは、単一プロバイダ依存を避け、OpenAI、Anthropic、Google Gemini、Ollama、Cohere、Tavily などを組み合わせたハイブリッド構成を実現するための設計仕様を定義しています。

## 概要

現代の AI システムでは、推論、検索、画像生成、音声処理、RAG、ツール呼び出し、コード実行などがそれぞれ別のプロバイダ・サービスに分かれており、運用時には次のような課題が発生します。

- プロバイダごとに API 仕様が異なる
- 同時に複数モデルを使い分ける運用設計が必要になる
- 例外処理、リトライ、フォールバックの実装が重複する
- コストとレイテンシを横断的に追跡したい
- ローカル実行とクラウド API を同一インターフェースで扱いたい

OmniAI SDK は、これらの課題に対して次の方向性を目指します。

- 10 領域の機能を共通インターフェースで抽象化する
- 同期 / 非同期 API の両方を提供する
- 型付きのマルチモーダル入力をサポートする
- 例外を統一し、フォールバック機構を組み込む
- コスト・レイテンシ・トークン消費を観測可能にする

## 主な設計方針

### 1. プロバイダ統合

OmniAI SDK は、以下のような機能領域を横断的に扱える設計を目指します。

- テキスト / マルチモーダル対話
- 判定モデル（Judge）
- 画像生成
- 音声処理（TTS / STT）
- 埋め込み
- リランカー
- Web 検索
- 文書解析
- コード実行
- MCP / 外部ツール連携

### 2. 統一 API

各機能に対して、共有の能力インターフェースを設け、以下を標準化します。

- 同期メソッドと非同期メソッド
- ストリーミング対応
- 例外の統一化
- 返却メタデータの標準化
- モデル切り替え可能な構成

### 3. 実運用向けの信頼性

本 SDK は、単なる実験用ラッパーではなく、実運用を想定した設計を採用しています。

- 自動フォールバック
- レート制限時の再試行
- コンテキスト長超過時の対応
- 監視 / メトリクス収集
- 本番環境での安全な例外制御

## リポジトリ構成

```text
OmniAISDK/
├── src/omni_ai/            # SDK 本体（コア＋各機能のアダプター）
├── tests/                  # unit / contract / live（live は環境変数で有効化）
├── docs/
│   ├── マルチプロバイダ統合Python SDK (OmniAI SDK) 設計仕様書.md   # 仕様書 v3.1
│   └── OmniAI SDK 実装設計書 (コア＋Chat).md                      # 実装設計
└── pyproject.toml
```

## 設計書

- [設計仕様書 v3.1](./docs/マルチプロバイダ統合Python%20SDK%20(OmniAI%20SDK)%20設計仕様書.md)
- [実装設計書 (コア＋Chat)](./docs/OmniAI%20SDK%20実装設計書%20(コア＋Chat).md)

## 現在のステータス（0.1.0 開発中）

| 領域 | 状態 |
| :--- | :--- |
| コア（例外・型・設定・OS差分吸収・HTTP 基盤・再試行・ルーター・テレメトリ・ToolRunner） | 実装済み |
| Chat: Ollama（ネイティブ `/api/chat`） | 実装済み・実 API で確認済み（`gemma4:31b-cloud`） |
| Chat: Google Gemini（`generateContent` v1beta） | 実装済み・実 API で確認済み（`gemini-3.1-flash-lite`。google_search は quota の都合で未確認） |
| Chat: xAI / Agnes / OpenRouter / OpenAI 互換サーバー（`/chat/completions`）・Ollama Cloud（ネイティブ） | 実装済み。OpenAI 互換サーバーはローカル Ollama の `/v1` で実物確認済み。xAI・Agnes・OpenRouter・Ollama Cloud は**偽物のテストのみ**（キーが無い。実装設計書 §25） |
| Embedding: Gemini / Ollama / fastembed（プロセス内・ONNX） | 実装済み・3 つとも実物で確認済み |
| Search: ddgs（キー不要の横断検索） | 実装済み・実物で確認済み（Ollama の WEB_SEARCH 代行も end-to-end で確認） |
| Rerank: fastembed（プロセス内・多言語クロスエンコーダー） | 実装済み・実物で確認済み（キー不要の RAG: 検索 → 埋め込み → 再順位付け） |
| DocParser: pypdf（PDF）/ markitdown（Office・RTF） | 実装済み・実物の文書ファイルで確認済み |
| MCP: 公式 SDK 2.x（stdio / streamable-http） | 実装済み・実際の MCP サーバーで確認済み（Ollama から MCP ツールを呼ぶ live 確認） |
| Judge: Ollama の decision モデル（`/v1/systemone`、要 Ollama 0.35+） | 実装済み・実物で確認済み（tev1:0.8b / kev:0.8b-q8_0） |
| Audio: 合成 edge-tts（キー不要・オンライン）/ 認識 faster-whisper（ローカル） | 実装済み・実物で確認済み（日本語の合成 → 認識の往復） |
| Image | **保留**（Ollama は v0.32.6 で画像生成を削除、Gemini は課金の設定が必要。実装設計書 §21） |
| Sandbox: ローカルの Docker（ネットワーク遮断・読み取り専用・資源制限） | 実装済み・実物で確認済み（Ollama の CODE_EXECUTION 代行も end-to-end） |

## インストールと設定

詳しくは [docs/install.md](./docs/install.md)。

```bash
pip install -e ".[local,search,docs,mcp,audio]"
omni-ai            # メニュー方式のセットアップ
omni-ai doctor     # 環境の診断
```

設定ファイル（`config.toml` / `secrets.toml`）を作れば、`OmniClient.from_config()` で組み立てられます。

### 環境変数で切り替える（前段プロキシを挟む運用）

起動時の環境変数で TOML を上書きできます。優先順位は **引数 → 環境変数 → TOML → 既定**。詳細は `src/omni_ai/env_overlay.py`。

| 変数 | 用途 |
|---|---|
| `OMNI_CHAT_PROVIDER` | 使う chat プロバイダ（`gemini` / `ollama` / `openai-compatible` など）に差し替える |
| `OMNI_CHAT_MODEL` | 選んだプロバイダのモデル |
| `OMNI_CHAT_BASE_URL` | 選んだプロバイダの接続先 URL |
| `OMNI_EMBEDDING_PROVIDER` / `_MODEL` / `_CACHE_DIR` | 埋め込みの差し替え |
| `OMNI_SEARCH_REGION` / `_SAFESEARCH` | 検索の切り替え |
| `OMNI_AUDIO_SPEECH` / `_TRANSCRIPTION` / `_VOICE` / `_WHISPER_MODEL` / `_CACHE_DIR` | 音声の切り替え（`speech=none` で合成を無効化） |
| `OMNI_JUDGE_MODEL` / `OMNI_DOC_PARSER_PROVIDER` | Judge・文書解析 |
| `OMNI_SANDBOX_IMAGE` / `_MEMORY` / `_NETWORK` | Sandbox |

接続先の API キーは従来どおり `OMNI_<PROVIDER>_API_KEY`（例: `OMNI_OPENAI_COMPATIBLE_API_KEY`）または各プロバイダ標準の環境変数。

#### 例: OpenAI 互換の前段プロキシへ向ける

PowerShell（Windows）::

    $env:OMNI_CHAT_PROVIDER = "openai-compatible"
    $env:OMNI_CHAT_BASE_URL = "http://127.0.0.1:8082"
    $env:OMNI_CHAT_MODEL    = "<プロキシが受けるモデル名>"
    $env:OMNI_OPENAI_COMPATIBLE_API_KEY = "unused-but-required"

前段プロキシ側で認証・モデル名の翻訳・ツール呼び出しの整形を担う運用（Claude Code 流儀の `ANTHROPIC_BASE_URL` と同じ発想）。SDK は環境変数だけで切り替わる。

## 使い方

```bash
pip install -e ".[dev]"          # ローカル埋め込み・検索・文書解析も使うなら ".[dev,local,search,docs,mcp,audio]"
```

```python
import asyncio
from omni_ai import ChatMessage, FallbackChatAdapter, OmniClient, omni_tool
from omni_ai.providers import GeminiChatAdapter, OllamaChatAdapter


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city.

    Args:
        city: city name
    """
    return "sunny, 20C"


async def main() -> None:
    chat = FallbackChatAdapter([
        GeminiChatAdapter(),                                  # GEMINI_API_KEY を参照
        OllamaChatAdapter(default_model="gemma4:31b-cloud"),  # Gemini が失敗したら Ollama へ
    ])
    async with OmniClient(chat=chat) as ai:
        result = await ai.tools.arun(
            [ChatMessage.user("東京の天気は？")], [get_weather]
        )
        print(result.response.content)
        print(ai.telemetry.by_provider())


asyncio.run(main())
```

## テスト

```bash
pytest -m "not live"                                    # 通常（ネットワーク不要）
OMNI_LIVE_OLLAMA=gemma4:31b-cloud pytest -m live tests/live   # 実 API（Ollama）
OMNI_LIVE_GEMINI=1 pytest -m live tests/live                   # 実 API（Gemini、要 GEMINI_API_KEY）
```

CI は GitHub Actions で 3 OS × Python 3.11〜3.13 を回す。

## ライセンス

このプロジェクトは MIT License の下で公開されています。詳細は [LICENSE](./LICENSE) を参照してください。

## 連絡先

本リポジトリは設計中心のプロジェクトとして進行中です。設計の修正、拡張、実装提案などは issue や PR で受け付けています。
