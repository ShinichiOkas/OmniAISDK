# 不具合票: tests/contract/test_mcp.py が 6 件まとめて失敗（既存）

- 作成: 2026-11-09 16:50 JST
- 原文: （師匠の依頼ではない。1.C 着手中に `pytest tests/unit tests/contract` を回して発見）
- 種類: 作る（直す）ではなく **票（見たことだけ）**
- 状態: ✅ 完了 2026-11-10 00:35（1.G 着手の前提として直した）
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

## 原因（2026-11-10 00:35 判明）

1. `tests/fixtures/mcp/demo_server.py` が古い API（`mcp.server.mcpserver.MCPServer`）を import していた。mcp SDK の現行版は `mcp.server.fastmcp.FastMCP` のみ
2. `src/omni_ai/providers/mcp/__init__.py` が `MCPError` を import していたが現行版は `McpError`（大文字小文字）
3. `ClientSession.call_tool(read_timeout_seconds=...)` の現行版は `datetime.timedelta` を要求するが、float を渡していた
4. FastMCP はツール内の例外の traceback を標準 logging 経由で stderr に流し、記号・改行の装飾がかかるので試験が探す文字列 `failed on purpose` と一致しなかった。fixtures 側で `print(..., file=sys.stderr)` を明示的に 1 行足して解決

## 対応（2026-11-10 00:35）

- fixtures: `FastMCP` へ API を更新し、`fail()` で stderr に 1 行を明示
- SDK: `McpError` に修正、`read_timeout_seconds` を `timedelta(seconds=...)` へ、timeout 検出を大小無関係に
- 試験: 9/9 緑

## 関連

- 進行中の依頼: `.pair-agent/requests/2026-11-09-1300-mnemo-replace-implementation.md`（Phase 1.C 作業中）
- SDK の MCP の差し込み口: Phase 1.G の 1.21〜1.24 で触る予定
