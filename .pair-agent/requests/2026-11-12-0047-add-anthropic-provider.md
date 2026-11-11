# AI プロバイダーに Anthropic を加える

**書き直したゴール（LLM が判定できる形）**: `anthropic` が Chat プロバイダーとして設定・builder・CLI・環境変数から使え、偽の上流（respx）で generate / stream / agenerate / astream / tools / 構造化出力 / 画像・PDF / 思考 / エラー写像の試験が通り、`ruff check .` と `mypy` と `pytest -m "not live"` が終了コード 0。実際の Anthropic API は鍵が無いので **未確認**（師匠の検収待ち）。

- 受けた時刻: 2026-11-12 00:47 JST
- 終わる時刻の宣言: 2026-11-12 03:30 JST までに完了（時刻になったら止まって報告）
- 原文: 「AIプロバイダーにAnthropicを加えてください」
- 種類: 作る
- 完了の定義（師匠の言葉から）: Anthropic が AI プロバイダー（Chat）として加わっている
- 状態: 作業中
- 確認欄: （空 = 未確認）
- 報告した時刻:

## 判断（推奨で進めた。覆したいものだけ言えばよい）
- J-A1: 公式 SDK は依存に入れず、既存の `_http`（httpx）で Messages API を直接叩く独自 codec（Gemini と同方式）。理由: 本リポジトリは SDK 非依存の sans-IO 構造。
- J-A2: 既定モデルは持たない（既存 J-02 に従い `models.anthropic` 必須）。
- J-A3: `max_tokens` は API 必須のため、指定が無ければ 16000 を送る（4096 だと思考で切れうる）。
- J-A4: 実 API は鍵が無く未確認。合成データの試験のみ。
