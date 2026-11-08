---
sprint_id: ebfeb020-b44c-4447-a40b-735d84c99cfd
version: 2
status: completed
created_at: 2026-11-08T06:00:00+09:00
updated_at: 2026-11-08T06:00:00+09:00
domain_tags: [python-sdk, image-generation, ollama]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

Ollama の画像生成（実験的・macOS のみ）を実物で調べ、使えるなら ImageCapability を実装する。

## 師匠の指示（原文 2026-11-08）

- 「了解。Ollamaの調査から開始してください」
- モデル: 「x/flux2-klein:4b（5.7GB） (Recommended)」

## 一次資料・調査（2026-11-08）

- 公式ブログ（2026-01-20）: 画像生成は実験的・macOS のみ。x/z-image-turbo（6B）、x/flux2-klein（4B: Apache 2.0、9B: 非商用）。API の記載は無い
- 第三者の資料: /api/generate に width / height / steps（実験的）、base64 の PNG が返る（フィールド名は未確認）
- レジストリのサイズ: flux2-klein:4b 5.73GB、:9b 11.97GB、z-image-turbo 12.77GB

## 調査結果（2026-11-08・Mac・Ollama.app 0.40.6）

- x/flux2-klein:4b の pull は成功し、capabilities は ["image"]
- しかし生成はどの経路でも動かない: /api/generate → 400 "image generation models are not currently supported"、/v1/images/generations → 404、/api/chat → 400 "does not support chat"、CLI（ollama run）→ 同じエラー
- 原因: リリースノート v0.32.6（2026-08-04）「Experimental image generation has been temporarily removed. Continue using 0.32.5 for image generation support」。v0.40.15 までの 40 件に復活の記載なし
- 0.32.5 に戻すと decision モデル（0.35.0 以上が必要）が使えなくなるため両立しない
- 結論: 現時点で Ollama による画像生成は実装できない（Gemini は 429・課金の設定が必要）

## タスク

- [x] flux2-klein:4b を pull し、/api/generate の実際の形を測る
- [ ] 使えるなら OllamaImageAdapter を実装・テスト・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |

## 変更ログ

- v1 [2026-11-08]: 初版
- 師匠（原文 2026-11-08）: 「保留してモデルを削除 (Recommended)」→ Image は保留。x/flux2-klein:4b を削除（/api/delete）
- v2 [2026-11-08]: 調査のみで完了（実装せず）
- 師匠（原文 2026-11-08）: 「../Mnemo/でagnesを用いた画像生成があった気がするので確認して」→ 確認: Mnemo に Agnes の画像生成の実装は無い（agnes-image-* はモデル一覧にあるが除外。記録は画像入力の話）
- 師匠（原文）: 「では画像は保留。sandboxに移りましょう」
