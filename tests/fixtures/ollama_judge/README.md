# Ollama decision (judge) fixtures

実物から記録（2026-11-08、師匠の Mac、Ollama.app 0.40.6、`tev1:0.8b` / `kev:0.8b-q8_0`）。`*.request.json` が送った要求。

実測で分かったこと:
- `POST /v1/systemone`。応答は `answers.<name>` に `type` と結果。`usage` は `input_tokens` / `output_tokens`（output は 0）、`prompt_eval_cached_count`
- noul: `{"noul": p}`。criteria は省略できた
- choice: `{"choice", "probabilities", "confidence"}`。説明が空だと tev1 は 400（"requires nonempty candidate descriptions"）
- score: `{"score"（段階番号の確率加重平均）, "legend"（"0": 段階名…）, "probabilities"（"0": p…）, "confidence"}`
- エラー: チャット用モデル → 400 `is not a decision model`、未知のモデル → 404、type 不正・選択肢 1 つ・質問 0 件 → 400
- 質問名の制限（資料ではクラウドの制限）はローカルでは掛からなかった（"bad name!" が通った）
