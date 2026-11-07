---
sprint_id: f83df133-41d1-467c-8589-08418958ec41
version: 2
status: reviewing
created_at: 2026-11-08T00:00:00+09:00
updated_at: 2026-11-08T00:00:00+09:00
domain_tags: [python-sdk, search, tools]
change_count_premise: 1
change_count_improvement: 0
---

# スプリントゴール

SearchCapability を ddgs（Mnemo の標準）で実装し、ToolRunner の WEB_SEARCH 代行を実物の検索で end-to-end に通す。

## 師匠の指示（原文 2026-11-08）

- 「はい。それで良いです。」（Search を ddgs で）
- 「ところで各機能においてプロバイダーのものでなくMnemo同様の物を選ぶときはプロバイダー名は何になる？Mnemoという名称は使わない方がいい。」→「エンジン/サービス名」「コードからは全部外す」
- ddgs が横断検索だと判明 →「"ddgs"（横断のまま）」（前提の訂正: change_count_premise=1）

## タスク

- [x] 実測（結果 0 件・エラー・レート制限の形）
- [x] DDGSSearchAdapter（provider_name "ddgs"、extras [search]）
- [x] OmniClient.search の計測、ToolRunner からの利用
- [x] テスト（記録した結果・live）・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| S-01 | 既定値は Mnemo と同じ（auto / ja-jp / safesearch on / 上限 50） | Mnemo の標準に揃える |
| S-02 | 結果 0 件は例外にせず空の結果 | 実測で DDGSException("No results found.") |
| S-03 | ddgs の例外は名前で写像し、RetryPolicy（2 回・2 秒）で再試行 | ライブラリを import せずにテストできる |
| S-04 | OmniClient.search も計測し、ai.tools の検索代行にもそれを使う | テレメトリに検索も載せる |
| S-05 | Mnemo の url_guard は取り込まない（残留リスクに記録） | Mnemo 固有の方針で、今回の範囲外 |

## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 実装・live 確認（Ollama ＋ 実検索の end-to-end を含む）
