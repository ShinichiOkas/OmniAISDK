---
sprint_id: 60b6b46e-2f01-4aab-ba10-cc208e05a903
version: 3
status: done
created_at: 2026-11-08T15:30:00+09:00
updated_at: 2026-11-08T16:00:00+09:00
closed_at: 2026-11-08T16:00:00+09:00
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

- [x] OpenAI 互換 Chat の codec（sans-IO）と共通アダプター（担当: AI）
- [x] xai / agnes / openrouter / openai-compatible の各アダプター（設定差分のみ）（担当: AI）
- [x] ollama-cloud（既存 Ollama アダプターの provider 名違い）（担当: AI）
- [x] 設定ファイル・builder・doctor・メニュー・単価判定への追加（担当: AI）
- [x] 契約テスト（偽サーバー）・規約テスト・ドキュメント（設計書・仕様の変更履歴・install.md）（担当: AI）

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
- v2 [2026-11-08]: 実装・契約テスト・ドキュメント。openai-compatible をローカル Ollama の /v1 で実物確認
- v3 [2026-11-08T16:00+09:00]: 検収完了・クローズ（クラウド 4 件は API キー未保有のため未確認で残置）

## おまかせで判断したこと（事後報告）

- J-01: 共通の codec を 1 つ作り、xAI / Agnes / OpenRouter / openai-compatible はクラス属性（接続先・キーの要否・構造化出力の方式）の違いだけにした
- J-02: Gemini 以外は既定のモデルを持たない（モデル名の入れ替わりが速い。Mnemo も既定なし）。設定の検証で models.<名前> が無ければ誤り
- J-03: Agnes の構造化出力は json_object を既定に（json_schema は未確認）。provider_options で切り替え可
- J-04: **どの方式でもスキーマを指示文で添える**（実測: Ollama /v1 は json_schema を黙って無視した）
- J-05: OpenRouter の帰属ヘッダーは付けない（Mnemo の師匠決定に倣う。必要なら extra_headers=）
- J-06: 応答の model ではなく指定したモデル名を記録（ルーティング先の名前で返るため。単価の照合を安定させる）
- J-07: usage の推論トークンを completion_tokens から分ける（OpenAI の定義では内数。単価計算での二重計上を防ぐ）
- J-08: ollama-cloud / openai-compatible / openrouter などは無料扱いにしない（単価表に無ければ None）
- J-09: Ollama Cloud は /api/show の能力確認とコンテナ向けの接続先切り替えをしない
- J-10: 環境変数 OMNI_<P>_<K> は名前のハイフンを `_` に（OMNI_OLLAMA_CLOUD_API_KEY）。各社の変数は Mnemo と同じ名前（XAI_API_KEY / AGNES_API_KEY / OPENROUTER_API_KEY / OLLAMA_API_KEY）。Mnemo の CUSTOM_API_KEY は名前が汎用すぎるので読まない
- J-11: メニューのモデル一覧は、取れて 30 件以下なら選択式、それ以外は手入力（OpenRouter は数百件）
- J-12: キー無しで試せる相手として、ローカル Ollama の /v1 で openai-compatible を実物確認した（師匠の「偽物のテストだけ」はキーの無いクラウド 4 つに適用と解釈）
