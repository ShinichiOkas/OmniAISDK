---
sprint_id: 8ae92190-c6db-4452-a1c7-dc08fba812dd
version: 2
status: executing
created_at: 2026-11-08T09:00:00+09:00
updated_at: 2026-11-08T09:00:00+09:00
domain_tags: [python-sdk, install, configuration, cli]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

インストールとコンフィグレーションを整備する。

## 師匠の指示（原文 2026-11-08）

- 「次のテーマに進もう。」→ 選択肢に無い回答「インストールとコンフィグレーションの整備」
- 範囲（複数選択）: 「インストール手順と前提の確認」「設定ファイルで OmniClient を組み立て」「設定の一覧と検証」「モデルのダウンロード管理」＋自由記述「それらのコンフィグレーションをガイドするメニュー方式のインストーラ」

## 範囲

1. インストール手順と前提の確認（文書化＋足りないものを教える診断コマンド）
2. 設定ファイルから OmniClient を組み立てる（OmniClient.from_config）
3. 設定項目の一覧と検証（誤った設定を分かりやすく弾く）
4. モデルのダウンロード管理（取得・一覧・削除・場所と容量）
5. 1〜4 を案内するメニュー方式のインストーラ

## 師匠の決定（2026-11-08）

- 入口: 「omni-ai コマンド＋メニュー (Recommended)」（引数なし → 番号で選ぶメニュー、引数つき → doctor / config / models を直接）
- API キー: 「別の秘密ファイル (Recommended)」（OS 標準の設定場所の secrets.toml。macOS / Linux は権限 600）
- 自動導入: 「Python 側だけ (Recommended)」（extras とモデルまで。Ollama / Docker 本体は案内のみ。どれも実行前に内容と容量を表示して確認）

## 進め方

おまかせ（3 段に分け、段ごとにコミット・プッシュ）
1. 設定ファイル・秘密ファイル・検証・OmniClient.from_config()
2. 環境の診断（doctor）とモデル管理
3. omni-ai コマンドとメニュー、インストール手順書

## 未決事項

- なし（細部はおまかせ。判断は下の一覧に集める）

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| I-01 | 設定の置き場所は OS 標準（platformdirs）、OMNI_CONFIG_DIR で変更可 | ADR-04・テストや持ち運びのため |
| I-02 | 解決の順を「引数 → OMNI_* → 各社の環境変数 → secrets.toml → config.toml」に | 秘密ファイルを設定ファイルより優先 |
| I-03 | 構成は [client.<機能>] に書く。未知のキーは誤りとして弾く（extra=forbid） | 誤字を黙って無視しない |
| I-04 | config.toml の api_key は読めるが警告 | 互換と安全の両立 |
| I-05 | TOML の書き出しは自前（依存を増やさない） | 読み込みは標準の tomllib |
| I-06 | MCP は from_config で組み立てない | 接続の開閉が非同期でサーバーごと |


## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 未決事項を決定、実行へ
