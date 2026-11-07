---
name: 外部 API は、資料を読むだけでなく実応答を記録してテストの前提にする
description: プロバイダのアダプターを書くとき、実 API の応答を fixtures として記録し、契約テストの前提にする。手作りの応答は「記録できないケース」に限り、手作りと明記する
type: technique
maturity: forming
occurrences: 4（2026-11-07 OmniAISDK: Ollama Chat / Gemini Chat / Gemini Embedding / Ollama Embedding）
---

資料と実物の差は毎回見つかった（いずれも 2026-11-07 の実測）:
- Ollama: `format` に JSON Schema を渡しても ```json のフェンスで包まれた／ツール呼び出しに資料に無い `id` が付いた
- Gemini: 資料では 400 のはずの prefill が gemini-3.1-flash-lite では 200／text の part にも thoughtSignature が付いた／署名を外すと本当に 400
- 埋め込み: Ollama は空入力に 200 で `embeddings: []`（件数がずれる）、長文は黙って切り詰め。Gemini-embedding-001 は usage を返さない

**How to apply:**
- 記録スクリプトはスクラッチに置き、`tests/fixtures/<provider>/` に応答と `*.request.json`（送った要求）と README（取得日・モデル・機械・分かったこと）を置く。キーが記録に入っていないことを assert する
- 「SDK が組み立てた要求」と「実 API で 200 になった要求」を突き合わせるテストを 1 本入れる（Gemini の並列ツール往復で有効だった）
- 記録できないケース（402・503 など）だけ手作りにし、ファイル先頭で「手作り」と明記する
