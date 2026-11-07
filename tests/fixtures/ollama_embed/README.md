# Ollama embedding fixtures

実 API から記録（2026-11-07、師匠の Mac、Ollama 0.17.7、`nomic-embed-text` 274.3MB を pull）。

実測で分かったこと:
- 単体・複数入力とも `embeddings: [[...]]`。768 次元。`dimensions` で縮められる（256 を確認）
- **空文字列・空リストは 200 で `embeddings: []`**（入力と件数がずれる）
- 文脈長（2048）を超える入力は、既定では**黙って切り詰める**（`prompt_eval_count=2048`）。`truncate:false` なら 400 `the input length exceeds the context length`
- チャット用モデルを指定すると 404 `model "x" not found, try pulling it first`（紛らわしい）
