---
sprint_id: 21bb322a-3752-422d-a286-5a427d646892
version: 2
status: reviewing
created_at: 2026-11-08T03:00:00+09:00
updated_at: 2026-11-08T03:00:00+09:00
domain_tags: [python-sdk, mcp, tools]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

MCPCapability を mcp SDK（Mnemo の標準 mcp>=2,<3）で実装し、MCP サーバーのツールを ToolRunner から呼べるようにする（仕様書 Layer 3）。

## 師匠の指示（原文 2026-11-08）

- 「OK.次、MCPに進みましょう」

## Mnemo の実装から取り込む教訓（読んだだけ・変更なし）

- 接続失敗は anyio の TaskGroup で ExceptionGroup に包まれる → 展開して本当の原因を出す
- call_tool に応答待ちの上限（Mnemo は 120 秒）
- Image / Audio / blob の結果（base64）をテキストに混ぜない
- mcp 2.x は属性名が snake_case（mime_type）、1.x は camelCase（mimeType）

## タスク

- [x] MCPClientAdapter（stdio / streamable-http）
- [x] ツール一覧 → ToolDefinition / OmniTool（ToolRunner 連携）、結果の型
- [x] テスト（ローカルの MCP サーバーを実際に起動）・live（Ollama ＋ MCP ツール）・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| M-01 | provider_name は "mcp"（どのサーバーかは name で区別） | 接続先は利用者ごとに変わるため、プロトコルの名前にした |
| M-02 | トランスポートは stdio と streamable-http。SSE は扱わない | SSE は旧方式 |
| M-03 | ツールのエラーは例外にせず is_error。ToolRunner へは {"error": ...} で返す | 実測で SDK もそう返す。モデルが自分で直せる |
| M-04 | サーバーの stderr は既定でログファイルに残す | Mnemo の教訓 |
| M-05 | CI に [mcp] を入れ、テスト用サーバーを 3 OS で起動する | 実物で確かめる。型チェックの ignore も一貫させるため |
| M-06 | elicitation / sampling / OAuth / リソース購読は扱わない | 今回の範囲は「ツールを呼べる」まで |

## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 実装・確認（Ollama から MCP ツールを live で）
