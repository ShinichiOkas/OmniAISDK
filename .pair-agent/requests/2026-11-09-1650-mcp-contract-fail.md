# 不具合票: tests/contract/test_mcp.py が 6 件まとめて失敗（既存）

- 作成: 2026-11-09 16:50 JST
- 原文: （師匠の依頼ではない。1.C 着手中に `pytest tests/unit tests/contract` を回して発見）
- 種類: 作る（直す）ではなく **票（見たことだけ）**
- 状態: 未着手（依頼『Mnemo → OmniAISDK 置き換え 実装』が閉じるまで、この票の直す役は起こさない）
- 発見のきっかけ: Phase 1.C 試験後の regression 確認。直前の feature/phase1b-webfetch HEAD（a6cf740）でも同じ 6 件が落ちるため、**1.C とは無関係の既存の失敗**。

## 見たこと（事実のみ）

- 失敗したファイル: `tests/contract/test_mcp.py`
- 落ちた試験（6 件）:
  - `test_list_and_call_tools`
  - `test_tool_errors_are_results_not_exceptions`
  - `test_image_is_attachment_not_text`
  - `test_call_timeout`
  - `test_prefix_and_not_connected`
  - `test_tool_runner_uses_mcp_tools`
- 共通エラー: `omni_ai.exceptions.ProviderConnectionError: MCP server 'demo': McpError: Connection closed (provider=mcp)`
- 直前のエラー: `mcp.shared.exceptions.McpError: Connection closed`
- 発生箇所: `src/omni_ai/providers/mcp/__init__.py:194`
- 版: feature/phase1b-webfetch @ a6cf740（1.C 変更を stash して確認済み）
- 他の試験は緑: unit 160 passed + contract 170 passed = 336 passed, 4 skipped

## 完了の定義

- 本物の MCP demo server 経路で 6 件とも緑になる
- または、既知の環境依存なら `pytest.mark.skipif` を正当な理由つきで付ける（Windows の tmpdir の MCP stdio サーバー起動の問題かもしれないが、**調査は票の仕事ではない**。直す役の仕事）

## 関連

- 進行中の依頼: `.pair-agent/requests/2026-11-09-1300-mnemo-replace-implementation.md`（Phase 1.C 作業中）
- SDK の MCP の差し込み口: Phase 1.G の 1.21〜1.24 で触る予定
