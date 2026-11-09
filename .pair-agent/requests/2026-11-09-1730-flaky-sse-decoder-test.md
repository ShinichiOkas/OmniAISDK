# 不具合票: tests/unit/test_http.py::test_sse_decoder_is_split_invariant が揺れる

- 作成: 2026-11-09 17:30 JST
- 原文: （師匠の依頼ではない。1.D 作業中の regression 確認で発見）
- 種類: 票（見たことだけ）
- 状態: 未着手（依頼『Mnemo → OmniAISDK 置き換え 実装』が閉じるまで、この票の直す役は起こさない）

## 見たこと（事実のみ）

- 試験: `tests/unit/test_http.py::test_sse_decoder_is_split_invariant`
- 1 回目の実行で `hypothesis` 由来のエラーで落ち、同じ版で 2 回目に実行すると通る
- 既存の試験。1.D の変更（`src/omni_ai/providers/fastembed/`）とは無関係

## 完了の定義

- hypothesis の例が安定するよう seed を固定するか、該当の例外を内部で捕まえて再試行する
- または hypothesis の strategy を狭める

## 師匠の方針

運転方針 11: 揺れるテストは即直す。ただし本件は依頼の外で見つけた課題のため、依頼台帳（置き換え実装）が閉じるまで直す役を起こさない（運転方針 9・2026-11-09）。
