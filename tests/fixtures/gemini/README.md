# Gemini fixtures

| 項目 | 値 |
| :--- | :--- |
| 取得日 | 2026-11-07 |
| 取得方法 | **実 API から記録**（`*.request.json` が送った要求）。キーは記録に含まれない |
| モデル | `gemini-3.1-flash-lite`（`generativelanguage.googleapis.com/v1beta`） |
| 取得した機械 | 師匠の Mac |

実測で分かったこと:
- `thoughtSignature` を外して関数呼び出しの履歴を返すと **HTTP 400**（`tools_followup_nosig.json`）。署名の保持は必須
- 関数呼び出しでない text の part にも `thoughtSignature` が付く
- ストリームの最後のチャンクは、空の text ＋ `thoughtSignature` ＋ `finishReason`
- 最後が model のターン（prefill）を送っても、このモデルは **200** で続きを返した（`prefill.json`）。資料の「3.6+ は 400」とは別
- キー不正は 400 ＋ `API_KEY_INVALID`、モデル不在は 404、google_search は 429（quota 超過・`RetryInfo` なし）

記録できないもの（402・403・500・503・`RetryInfo`・出典付きの grounding・ブロック）は、
`tests/contract/test_gemini.py` の中で資料の例から**手作り**した応答を使っている。
