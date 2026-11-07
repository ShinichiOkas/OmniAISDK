---
sprint_id: c47f60bc-740f-4bae-b532-23e27cbd25b1
version: 1
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

- [ ] M0 雛形（pyproject・src レイアウト・ruff / mypy / pytest・3 OS の CI）
- [ ] M1 exceptions / types / capabilities / config / compat
- [ ] M2 _http（transport・RetryPolicy・SSE / NDJSON）・_media
- [ ] M3 OllamaChatAdapter
- [ ] M4 GeminiChatAdapter
- [ ] M5 router / pricing
- [ ] M6 tools（omni_tool / ToolRunner）
- [ ] live: gemma4:31b-cloud で Ollama の経路を実測する（R-07）

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

## 変更ログ

- v1 [2026-11-07T16:30:00+09:00]: 初版
