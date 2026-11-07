---
sprint_id: 127bc088-cabd-4dd7-929c-6f73c136c5c0
version: 3
status: reviewing
created_at: 2026-11-08T01:00:00+09:00
updated_at: 2026-11-08T01:00:00+09:00
domain_tags: [python-sdk, rerank, local-model, rag]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

RerankCapability をローカル（fastembed のクロスエンコーダー）で実装し、検索 → 埋め込み → 再順位付けを、キーなしで end-to-end に通す。

## 師匠の指示（原文 2026-11-08）

- 「進めてください」（Rerank のローカル実装）
- 既定モデル: 「bge-reranker-v2-m3-int8 (Recommended)」

## ライセンスの確認（2026-11-08）

- 元モデル BAAI/bge-reranker-v2-m3: Apache-2.0（Hugging Face の API で確認）
- 実際に落とすのは onnx-community/bge-reranker-v2-m3-ONNX の onnx/model_int8.onnx（570,727,094 バイト）＋ tokenizer.json（17,082,900 バイト）。**変換版リポジトリにはライセンスの明記が無い**（元モデルの Apache-2.0 を引き継ぐと考えるのが自然だが未確認）。fastembed のカタログ値は apache-2.0

## タスク

- [x] FastEmbedRerankAdapter（provider_name "fastembed"）
- [x] OmniClient.rerank の計測
- [x] テスト（偽モデル・live）・RAG の end-to-end（ddgs → fastembed 埋め込み → rerank）
- [x] ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| R-01 | スコアは既定でシグモイドを通して 0〜1。raw_scores で生の値 | 実測でロジットだった。クラウドの Rerank と揃える |
| R-02 | 埋め込みと読み込み処理を共通化（LazyModel） | 同じ fastembed の流儀 |
| R-03 | 変換版リポジトリのライセンス未明記を残留リスクとして記録し、採用は進めた | 元モデルは Apache-2.0、師匠の選択済み |
| R-04 | live の RAG テストで、埋め込みモデルが既定キャッシュに重複ダウンロードされた（約 240MB）。以後は共有キャッシュを使うよう直した | 削除は師匠に確認する |

## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 実装・live 確認（キー不要の RAG を end-to-end）
- 師匠（原文 2026-11-08）: 「重複モデルは削除しておいて」→ ~/Library/Caches/omni_ai/models から埋め込みモデル（Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q、共有 blobs 18/・e2/ 計 240MB）を削除。.refs で参照元がこのモデルだけと確認してから削除し、rerank モデルがオフラインで動くことを確認（561MB 残存）
- v3 [2026-11-08]: R-04 解消
