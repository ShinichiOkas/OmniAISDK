---
sprint_id: 680d057e-aa47-4d4f-8655-32d13eb30ecf
version: 2
status: reviewing
created_at: 2026-11-08T04:00:00+09:00
updated_at: 2026-11-08T04:00:00+09:00
domain_tags: [python-sdk, judge, ollama, decision-model]
change_count_premise: 1
change_count_improvement: 0
---

# スプリントゴール

JudgeCapability を Ollama の decision モデル（/v1/systemone）で実装し、実物で確かめる。

## 師匠の指示（原文 2026-11-08）

- 「OllamaにJudgeモデルがあるはずなのでそれで試して」（前提の訂正: AI は LLM-as-a-Judge を提案していた → change_count_premise=1）
- Ollama の版: 「アプリ版に切り替え (Recommended)」→ Homebrew 版 0.17.7 のサーバー（PID 64618）を止め、Ollama.app 0.40.6 を起動。既存の 8 モデルが残っていることを確認
- モデル: 「tev1:0.8b」「kev:0.8b-q8_0」（nimble 9.5GB は見送り）→ 834MB / 812MB を pull。capabilities はどちらも ["decision"]

## 一次資料（2026-11-08）

- https://docs.ollama.com/capabilities/decision.md : v0.35.0 以上。POST /v1/systemone。question の type は choice / noul / score

## タスク

- [x] /v1/systemone の実測と記録
- [x] OllamaJudgeAdapter（JudgeCapability）と型の見直し
- [x] テスト・live・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| J-01 | NoulQuestion に criteria（省略可）を追加 | 実測で API が持つ |
| J-02 | score の distribution は段階名で引けるようにした | legend を使って番号 → 名前 |
| J-03 | choice の説明が空でも SDK は補わずに送り、400 を例外にする | 意味を勝手に変えない |
| J-04 | Ollama のアダプターは、応答でなく指定したモデル名を記録する | 実測: 0.40.6 で -cloud が落ち、クラウドを無料と誤判定していた |
| J-05 | 質問名の検査はしない | ローカルでは制限が掛からない（資料の制限はクラウド向け） |

## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 実装・live 確認。Ollama 0.40.6 でのモデル名の変化に対応
