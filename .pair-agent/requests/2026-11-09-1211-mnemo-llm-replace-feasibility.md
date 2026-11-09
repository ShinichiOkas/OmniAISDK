# Mnemo の OmniAISDK 機能全域リプレース可否調査

- 受けた時刻: 2026-11-09 12:11 JST
- 原文（初出）: 「OKでは、../Mnemo/の実装を確認し、本プロジェクトでMnemoのLLMを完全リプレースできるか、そのためには何をしなくてはならないかを手ってい調査。レポートして。」
- 原文（追加・スコープ拡張 2026-11-09）: 「Goal set: embedding, tts, web search , web fetch など本SDKが提供する機能でMnemoが独自実装していることは多岐にわたる。LLMだけで見ずに、本SDKが提供している機能で、Mnemoが独自実装しているものを洗い出し、それをリプレースするために何が必要かの完全なレポートをmd型式で作成、報告すること」
- 種類: 究明（調査レポート・md 成果物）
- 完了の定義（師匠の言葉から）: OmniAISDK が提供する機能（LLM・embedding・TTS・web search・web fetch 等）について、Mnemo の独自実装を洗い出し、リプレースに必要なことを完全なレポートとして md で作成し師匠に報告している
- 状態: 完了（究明＝レポート提示）→ 続いて実装の依頼（別件）に着手
- 成果物: `.pair-agent/requests/2026-11-09-1211-mnemo-omniaisdk-replace-report.md`・`.pair-agent/requests/2026-11-09-1240-mnemo-replace-todo.md`
- 確認欄: レポートと TODO を師匠に提示済み・方針を師匠が承認（ELLM 除外・環境変数切替・既存 ellm-proxy 流用・SDK 先行・AI 見積もり基準）
- 報告した時刻: 2026-11-09 12:40 JST
- 終了の宣言: 2026-11-09 14:00 JST までに md を書き上げて報告（12:40 完了）
- 後続依頼: 「順に着手。着手・完了は TODO の表にステータス追記」→ TODO 側で管理
