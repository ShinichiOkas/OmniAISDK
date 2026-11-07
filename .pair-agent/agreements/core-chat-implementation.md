---
sprint_id: c47f60bc-740f-4bae-b532-23e27cbd25b1
version: 2
status: executing
created_at: 2026-11-07T16:30:00+09:00
updated_at: 2026-11-07T16:30:00+09:00
domain_tags: [python-sdk, implementation, llm-provider]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

承認済みの実装設計書（docs/OmniAI SDK 実装設計書 (コア＋Chat).md）に従って、M0〜M6 をできるところまで実装する。

## 師匠の指示（原文 2026-11-07）

- 「振り返りは省略。できるかぎりおまかせで自走。しばらく離席します」
- 前スプリントからの継続: 「できるだけ頻繁にコミット、プッシュ」（confirmed Skill）、「R-08 まずは保留で実装を進めて後で解決」、「gemma4:31b-cloudを使用」

## スライスサイズ

おまかせ（実装は親が行い、各マイルストーンの区切りでテストを通してコミット・プッシュする。判断は下の一覧に集めて事後報告する）

## タスク

- [x] M0 雛形（pyproject・src レイアウト・ruff / mypy / pytest・3 OS の CI）
- [x] M1 exceptions / types / capabilities / config / compat
- [x] M2 _http（transport・RetryPolicy・SSE / NDJSON）・_media
- [x] M3 OllamaChatAdapter
- [x] M4 GeminiChatAdapter
- [x] M5 router / pricing
- [x] M6 tools（omni_tool / ToolRunner）
- [x] live: gemma4:31b-cloud で Ollama の経路を実測する（R-07）

## 完了条件

- 各マイルストーンの単体・契約テストがローカル（Mac）で通る
- ruff と mypy が通る
- 区切りごとに origin/main へ push されている

## 既知リスク

- Gemini generateContent を実測できない（R-08・保留）→ Gemini の fixtures は資料の例から手で作ることになり、「手作り」と明記する
- CI は push してはじめて走る。Windows での挙動は CI の結果でしか確認できない

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| P-01 | 開発環境は uv で .venv（Python 3.11.2）を作る | 手元にあったため。CI は pip |
| P-02 | ruff の対象から Markdown を外した | ruff 0.17 が設計書のコード例まで整形したため（取り消し済み） |
| P-03 | Role / BuiltinTool を StrEnum にした | ruff の推奨。値は不変 |
| P-04 | ストリーム途中のエラーは、それより前のイベントを返してから送出する | テストで見つけた不具合の修正（設計書 §13 I-04） |
| P-05 | 構造化出力の検証前にコードフェンスを 1 組外す | Ollama の実測（I-03） |
| P-06 | Ollama のモデル起動失敗（runner failed 等）は再試行しない | 実測した 500 は何度送っても直らない |
| P-07 | Gemini の契約テストは資料の例から手作りした応答で書いた | R-08 |
| P-08 | 非同期の Files API アップロードはチャンクで流す | メモリ方針 |
| P-09 | supported_builtins をインスタンス属性にした（仕様書 D-18） | ラッパーが内側の値を名乗るため |
| P-10 | ToolRunner: handler の無い ToolDefinition は pending_tool_calls で返す | 呼び出し側で実行するツールと混在させるため |
| P-11 | live テストは環境変数で有効化。Ollama の live 5 件は実行して通過 | R-07 |
| P-12 | 実装の独立レビューを、別の文脈のエージェントに依頼した | 作った本人のテストは同じ盲点を共有するため |

## 変更ログ

- v1 [2026-11-07T16:30:00+09:00]: 初版
- v2 [2026-11-07]: M0〜M6 と Ollama の live 確認を完了。判断一覧を記録
