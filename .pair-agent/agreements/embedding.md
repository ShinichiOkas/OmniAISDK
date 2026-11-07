---
sprint_id: 9ed8169e-c3cb-46d8-ad4a-ebdaf90198d7
version: 4
status: completed
created_at: 2026-11-07T19:00:00+09:00
updated_at: 2026-11-07T19:00:00+09:00
domain_tags: [python-sdk, embedding, llm-provider, local-model]
change_count_premise: 0
change_count_improvement: 1
---

# スプリントゴール

EmbeddingCapability を実装する。Gemini・Ollama・ローカル（プロセス内）の 3 アダプター。

## 師匠の指示（原文 2026-11-07）

- 次の領域: 「Embedding (Recommended)」
- Ollama の埋め込みモデル: 「nomic-embed-text を pull (Recommended)」（274.3MB を確認して pull 済み）
- 追加: 「追加でローカル実装の埋め込みにも対応させて欲しい。../Mnemo/で標準的に使用している埋め込みを使いたい。」
  → Mnemo の標準 = fastembed（ONNX）＋ sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2、L2 正規化・float32、キャッシュ ~/.mnemo/models
- 進め方: おまかせ（前スプリントの「できるかぎりおまかせで自走」を継続）

## タスク

- [x] 実測（Gemini embedContent / batchEmbedContents、Ollama /api/embed）と記録
- [x] EmbeddingCapability の共通の決まり（空入力・正規化・task・バッチ分割）
- [x] GeminiEmbeddingAdapter / OllamaEmbeddingAdapter / LocalEmbeddingAdapter（fastembed、extras）
- [x] 契約テスト（記録した応答）・live テスト
- [x] ドキュメント（実装設計書・仕様書・README）

## 完了条件

- 3 アダプターが同じ契約テストを通る。live で 3 つとも実物を確認する
- ローカル実装が Mnemo と同じモデル・同じベクトル（正規化済み）を返す

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| E-01 | Gemini の既定埋め込みモデルを gemini-embedding-2 に | 実測で usageMetadata を返す（-001 は返さない） |
| E-02 | 正規化を既定 ON（normalize=True）に | Mnemo 互換・プロバイダ間で cosine を揃える |
| E-03 | task（query/document）を共通引数に追加 | Gemini の taskType を共通の書き方で使うため |
| E-04 | 空文字列は送信前に拒否、件数のずれはエラー | 実測: Ollama は空入力に embeddings: [] |
| E-05 | Ollama は /api/show で embedding 能力を事前確認 | 実測: チャットモデルだと紛らわしい 404 |
| E-06 | ローカル実装は extras [local]、既定キャッシュは OS 標準、Mnemo 共有は MNEMO_CACHE_DIR | コア依存を増やさない・Mnemo の既存モデルを再利用 |
| E-07 | CI には fastembed を入れず、ローカルの単体テストは偽のモデルで行う | CI でのモデルダウンロード（220MB）を避ける |

## 変更ログ

- v1 [2026-11-07]: 初版（途中でローカル実装の追加指示を受けて範囲を拡張: change_count_improvement=1）
- v2 [2026-11-07]: 3 アダプターを実装し、live で確認（Mnemo のベクトルとの比較 cos=0.9999999）
- 師匠（原文）: 「レビューは省略、有益なスキルがあったらそれだけ反映」
- v3 [2026-11-07]: スプリント完了。Skill を 4 件登録（ペア固有 2・プロジェクト固有 2）
- 師匠（原文 2026-11-08）: 「ところで各機能においてプロバイダーのものでなくMnemo同様の物を選ぶときはプロバイダー名は何になる？Mnemoという名称は使わない方がいい。」→ 決定「エンジン/サービス名」「コードからは全部外す」（confirmed Skill: provider-naming-engine-name-no-mnemo）
- v4 [2026-11-08]: LocalEmbeddingAdapter → FastEmbedEmbeddingAdapter（"fastembed"）、MNEMO_CACHE_DIR 削除
