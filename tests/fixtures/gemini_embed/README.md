# Gemini embedding fixtures

実 API から記録（2026-11-07、師匠の Mac、`gemini-embedding-001`）。`*.request.json` が送った要求。

実測で分かったこと:
- 既定は 3072 次元。`outputDimensionality` で縮められる（256 を確認）
- `batchEmbedContents` は models.list の対応メソッドに載っていないが動く
- `gemini-embedding-001` は `usageMetadata` を返さない。`gemini-embedding-2` は返す（`promptTokenCount`）
- 空文字列は HTTP 400（`EmbedContentRequest.content contains an empty Part.`）
