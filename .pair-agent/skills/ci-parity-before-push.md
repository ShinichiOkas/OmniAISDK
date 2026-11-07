---
name: 任意依存を足したら、CI と同じ素の環境で lint / 型 / テストを通してから push する
description: 開発環境にだけ入っている依存（extras）があると、ローカルでは通って CI で落ちる。CI と同じ依存だけの使い捨て venv で確かめる
type: technique
maturity: draft
occurrences: 1（2026-11-07 OmniAISDK: fastembed を extras に足して CI の mypy が全ジョブ失敗）
---

- 再現手順: `uv venv <scratch>/ci-venv && uv pip install -p <scratch>/ci-venv -e ".[dev]" && <scratch>/ci-venv/bin/mypy`
- 任意依存の import は mypy の `[[tool.mypy.overrides]] ignore_missing_imports` で扱い、実行時は ImportError で extras を案内する
- 関連する観測: ruff 0.17 は Markdown 内のコードブロックも整形する（設計書が書き換わった）→ `extend-exclude = ["docs", "*.md"]`

**How to apply:** pyproject の依存・extras・CI 設定を変えたコミットの前に、上の使い捨て venv で一度通す。
