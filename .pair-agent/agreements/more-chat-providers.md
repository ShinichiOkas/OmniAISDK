---
sprint_id: 60b6b46e-2f01-4aab-ba10-cc208e05a903
version: 1
status: executing
created_at: 2026-11-08T15:30:00+09:00
updated_at: 2026-11-08T15:30:00+09:00
domain_tags: [python-sdk, chat, providers, openai-compatible]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

../Mnemo が対応している LLM プロバイダーのうち、ELLM 以外で OmniAI に無いものを Chat に対応させる。

## 師匠の指示（原文 2026-11-08）

- 「../MnemoにあるELLM以外のプロバイダーに対応して」
- 範囲: 「Chat のみ (Recommended)」
- Custom: 「含める (Recommended)」（プロバイダー名は "openai-compatible"。"custom" は使わない）
- Ollama Cloud: 「ネイティブ API (Recommended)」（既存の /api/chat に base_url=https://ollama.com とキー）
- API キー: 「偽物のテストだけで進める」（実物での確認は未確認として残す）

## Mnemo の調査結果（読み取りのみ）

- 選択肢: gemini / xai / agnes / ollama / ollama-cloud / openrouter / ellm / ellm-lib / custom（scripted は検査用）
- OmniAI に無いもの: xai / agnes / openrouter / ollama-cloud / custom
- xai・agnes・openrouter・custom・ollama-cloud はいずれも OpenAI 互換 /chat/completions（Mnemo は openai SDK）
  - xai: https://api.x.ai/v1、XAI_API_KEY、json_object 対応
  - agnes: https://apihub.agnes-ai.com/v1、AGNES_API_KEY、json_object 対応（実測）、/models/{id} 未対応
  - openrouter: https://openrouter.ai/api/v1、OPENROUTER_API_KEY、帰属ヘッダーは付けない（Mnemo の師匠決定）、
    response_format 非対応モデルでも 200 で地の文を返す
  - ollama-cloud: https://ollama.com、OLLAMA_API_KEY（必須）
  - custom: URL 手入力（既定 URL なし・/v1 の有無を正規化）、キー任意、llama-server は model を無視

## タスク

- [ ] OpenAI 互換 Chat の codec（sans-IO）と共通アダプター（担当: AI）
- [ ] xai / agnes / openrouter / openai-compatible の各アダプター（設定差分のみ）（担当: AI）
- [ ] ollama-cloud（既存 Ollama アダプターの provider 名違い）（担当: AI）
- [ ] 設定ファイル・builder・doctor・メニュー・単価判定への追加（担当: AI）
- [ ] 契約テスト（偽サーバー）・規約テスト・ドキュメント（設計書・仕様の変更履歴・install.md）（担当: AI）

## 完了条件

- 5 つのプロバイダーで generate / stream / agenerate / astream・ツール呼び出し・構造化出力が契約テストで通る
- config.toml の chat.providers に 5 つを書いて from_config で組み立てられ、フォールバックに並べられる
- 全 OS の CI が通る
- 実物での確認は未確認として明記する

## 前提

- API キーは GEMINI のみ。実物での確認はしない（師匠決定）
- Mnemo は読み取りのみ。コードに "Mnemo" を書かない

## 既知リスク

- 実物の応答形の差（usage の有無、reasoning の項目名、tool_calls の分割の仕方）は偽物では捕まえられない
- Agnes の json_schema 対応は未確認 → 既定を json_object にする

## 変更ログ

- v1 [2026-11-08T15:30+09:00]: 初版（調査と師匠の回答を反映）
