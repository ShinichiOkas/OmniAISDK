# AI プロバイダーに Anthropic を加える

**書き直したゴール（LLM が判定できる形）**: `anthropic` が Chat プロバイダーとして設定・builder・CLI・環境変数から使え、偽の上流（respx）で generate / stream / agenerate / astream / tools / 構造化出力 / 画像・PDF / 思考 / エラー写像の試験が通り、`ruff check .` と `mypy` と `pytest -m "not live"` が終了コード 0。実際の Anthropic API は鍵が無いので **未確認**（師匠の検収待ち）。

- 受けた時刻: 2026-11-12 00:47 JST
- 終わる時刻の宣言: 2026-11-12 03:30 JST までに完了（時刻になったら止まって報告）
- 原文: 「AIプロバイダーにAnthropicを加えてください」
- 種類: 作る
- 完了の定義（師匠の言葉から）: Anthropic が AI プロバイダー（Chat）として加わっている
- 状態: 確認待ち（機械検査は通過。実 API は師匠の検収待ち）
- 確認欄（2026-11-12 01:05 JST）:
  - `python -m pytest -m "not live"` → 514 passed, 5 skipped（skip は既存の extras 未導入）。うち Anthropic は契約 38 件＋配線 5 件
  - 基準線との比較（git worktree で 92c3ed9 と今を同じ道具で実行し、行番号を除いて diff）: `ruff check .` は基準 99 件・今 99 件で**差 0**、`mypy` は基準 30 件・今 30 件で**差 0**。どちらも終了コードは非 0（既存コード由来。今回の変更による新規 0 件）。自分のファイルの ruff check / format は通過
  - 依頼の合否条件「ruff check . と mypy が終了コード 0」は**満たしていない**（既存の指摘が原因・票）。pytest は 0
  - 偽の上流（respx）で generate / stream / async / tools / 構造化出力 / 画像・PDF / 思考の往復 / エラー写像 / 再試行 / フォールバックを確認
  - **実際の Anthropic API は未確認**（鍵なし）。`tests/live/test_live_anthropic.py` は ANTHROPIC_API_KEY と OMNI_LIVE_ANTHROPIC_MODEL があれば走る（未実行）
- 報告した時刻: 2026-11-12 01:12 JST

## 判断（推奨で進めた。覆したいものだけ言えばよい）
- J-A1: 公式 SDK は依存に入れず、既存の `_http`（httpx）で Messages API を直接叩く独自 codec（Gemini と同方式）。理由: 本リポジトリは SDK 非依存の sans-IO 構造。
- J-A2: 既定モデルは持たない（既存 J-02 に従い `models.anthropic` 必須）。
- J-A3: `max_tokens` は API 必須のため、指定が無ければ 16000 を送る（4096 だと思考で切れうる）。
- J-A4: 実 API は鍵が無く未確認。合成データの試験のみ。
- J-A5: 呼び出し側が指定したものだけを送る（モデルごとの書き換えはしない）。
- J-A6: 応答の content ブロック列を provider_state に残し、thinking の署名を往復させる。
- J-A7: サーバーツール・strict は未対応（票）。
- J-A8: セットアップメニューは末尾追加。

## 票（依頼の外で見つけたもの・直していない）
- `STATIC_CONTEXT_WINDOWS` の Claude 4 系が古い可能性
- サーバーツール（web_search など）の対応
- 新しい ruff で既存コードに E501 / SIM105 等、mypy 30 件（fastembed / web_fetch）。CI の `ruff check .` が非 0
