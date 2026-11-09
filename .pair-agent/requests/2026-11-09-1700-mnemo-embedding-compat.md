# Mnemo 既存 DB との embedding 互換の確保

- 受けた時刻: 2026-11-09 17:00 JST
- 原文: 「1.Dに進むが、embedについてはMnemoの既存DBを作り直したくないので互換性が重要。互換を確保できる形で実装して」
- 種類: 作る（互換を保った実装）＋ 確かめる（既存ベクトルと一致すること）
- 完了の定義:
  1. Phase 1.D（1.15 numpy で返す道・1.16 トークナイザの共有）を、**Mnemo 既存 DB のベクトルと数値一致する**形で実装する
  2. 互換の確かめ: 本物の Mnemo のモデル（既定: `nomic-embed-text-v1.5` 等）で、同じ入力テキストに対して SDK の `FastEmbedEmbeddingAdapter` と Mnemo の `EmbeddingModel` が**余弦類似度 1 - 1e-6 以内**の同じベクトルを返す（Phase 3.1 の受入基準と同じ）
  3. 次元・float32 の bit 列・L2 正規化の有無・task（query/document）の既定が揃っていること
- 状態: 作業中
- 成果物: `src/omni_ai/providers/fastembed/` の変更、tests/unit/test_fastembed_compat.py（互換の数値確認）、TODO の 1.15・1.16 の状態欄
- 確認欄: Mnemo の `EmbeddingModel` と同じ入力でベクトルを並べ、`np.allclose(a, b, atol=1e-6)` が True になることを記す。既存 DB のサンプル ID でも引き直して一致を確かめる
- 終了の宣言: 本セッションは既に 17:00 を超過。次セッションで続行
