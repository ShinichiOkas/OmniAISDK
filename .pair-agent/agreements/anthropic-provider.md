---
sprint_id: 7d3e2a40-5b1c-4f60-9a2e-0112a0f10c47
version: 1
status: done
created_at: 2026-11-12T00:47:00+09:00
updated_at: 2026-11-12T01:30:00+09:00
domain_tags: [python-sdk, chat, providers, anthropic]
---

# スプリントゴール

AI プロバイダー（Chat）に Anthropic を加える。

## 師匠の指示（原文 2026-11-12）

- 「AIプロバイダーにAnthropicを加えてください」

台帳: `.pair-agent/requests/2026-11-12-0047-add-anthropic-provider.md`

## 判断（推奨で進めた。覆したいものだけ言えばよい）

- J-A1: 公式 SDK は依存に入れず、既存の `_http`（httpx）で Messages API を直接叩く独自 codec（Gemini と同方式）。理由: 本リポジトリは SDK 非依存の sans-IO 構造
- J-A2: 既定のモデルは持たない（J-02 に従い `models.anthropic` 必須）
- J-A3: `max_tokens` は API 必須。指定が無ければ 16000（4096 だと思考で切れうる）。`default_max_tokens=` と呼び出しごとの `max_tokens=` で変えられる
- J-A4: 実 API は鍵が無く未確認。合成データの試験のみ
- J-A5: 送るのは呼び出し側が指定したものだけ。新しいモデルが断るパラメータをモデル名で書き換えない
- J-A6: 応答の content ブロック列を `provider_state` にそのまま残して返す（thinking の署名）
- J-A7: サーバーツール（web_search など）は未対応（票）。`strict` は送らない
- J-A8: メニューの並びは末尾に追加（既存の番号を動かさない）
- J-A9: main へ直接 push（このリポジトリの履歴に合わせた。覆したければ言う）

## 実装設計書

`docs/OmniAI SDK 実装設計書 (コア＋Chat).md` §26。
