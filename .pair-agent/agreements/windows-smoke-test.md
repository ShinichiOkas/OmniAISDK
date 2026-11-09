---
sprint_id: windows-smoke-test
version: 2
status: closed
created_at: 2026-11-08T16:30:00+09:00
updated_at: 2026-11-08T18:30:00+09:00
closed_at: 2026-11-08T18:30:00+09:00
domain_tags: [windows, install, smoke-test, doctor]
---

# スプリントゴール

このプロジェクトを Windows 環境で**初回 clone から通して動かす**。インストール手順（`pip install -e .` → `omni-ai` メニュー）と主要機能（Chat・doctor・config・models）が実機で動くことを確かめる。

## 師匠の指示（原文 2026-11-08）

- 「このプロジェクトのWindows環境における動作確認がメイン」
- 「今初めてcloneしたのでinstall機能の確認もしたい」

## 実機の初期状態（確認済み）

- OS: Windows 11 Enterprise 10.0.26100
- Python 3.12.10（`py -3.12`）／ 3.14 もあり
- Ollama 0.40.12 導入済み
- Docker **未導入**（Sandbox は検査対象外にする）
- omni-ai **未インストール**
- 設定ディレクトリ `C:\Users\0000950207\AppData\Local\omni-ai\omni-ai` **未作成**
- API キー: `GEMINI_API_KEY` のみ想定（他は無し）

## 範囲

**対象（やる）**:

1. **インストール**: `pip install -e .` が通ること（コアのみ・extras なし）
2. **CLI 起動**: `omni-ai --help` / `omni-ai doctor` / `omni-ai config path`
3. **メニュー `omni-ai`**: 対話で `config.toml` / `secrets.toml` を作れること（Gemini + Ollama の最小構成）
4. **設定の検証**: `omni-ai config validate`
5. **モデル管理**: `omni-ai models catalog` / `list` / `size`（`pull` は手元の Ollama で軽量モデルを 1 つ）
6. **Chat の実機確認（2 本）**:
   - Gemini（`GEMINI_API_KEY` 要）: `generate` / `stream`
   - Ollama（手元の 11434）: `generate` / `stream`
7. **doctor の網羅性**: Docker 未導入・キー欠落のときに**場所と直し方**が出ること
8. **extras の 1 本**: `pip install -e ".[search]"` で `ddgs` が入ること（Web 検索の素振り）

**対象外（やらない）**:

- Docker Sandbox（Docker 未導入のため）
- fastembed / faster-whisper の大物モデル取得（容量が重い。別スプリント）
- xAI / Agnes / OpenRouter / Ollama Cloud の実物（キー無し）
- MCP サーバーの実機

## タスク（スライス: M・AI 単独で自走）

- [ ] T1: `pip install -e .` を 3.12 で通す（担当: AI）
- [ ] T2: `omni-ai --help` / `doctor` / `config path` が走ることを確認（担当: AI）
- [ ] T3: `omni-ai` メニューで最小の `config.toml` + `secrets.toml`（Gemini + Ollama）を作る。対話は AI がスクリプトで入力
- [ ] T4: `config validate` で誤りが場所つきで出ることを**わざと誤って**確かめる（providers に未知名を入れる等）
- [ ] T5: `models catalog` / `list` で表が出ること。`ollama pull` で 1 件（軽い既存モデル）
- [ ] T6: Gemini Chat（generate / stream）を実機で 1 回（キーがあれば）
- [ ] T7: Ollama Chat（generate / stream）を実機で 1 回
- [ ] T8: doctor が Docker 未導入・OpenRouter キー欠落などを**場所つきで**指摘することを確認
- [ ] T9: `pip install -e ".[search]"` → ddgs の import が通ること
- [ ] T10: 発見した欠陥は**見たことだけ**のバグ票に起こし、直す役を並列で起こす
- [ ] T11: install.md / README の Windows での引っかかりを追記（PowerShell での書き方など）

## 完了条件

- T1〜T9 の各タスクが**実機のログ付きで**合格
- 発見したバグはすべて票にして、修正スプリントに託すか、本スプリントで直して閉じる
- `omni-ai doctor` が最終状態で**健全**（Docker・未導入キーは「任意」扱いで黄色、致命は無し）
- 変更があれば push 済み

## 前提

- Python は 3.12（既定）
- Ollama は手元で稼働（既に 0.40.12 あり）
- Gemini のキーがあれば実機 Chat を 1 回だけ回す。なければスキップして未確認を明記
- 対話メニューは**非対話の手段**（環境変数・設定ファイル直書き・stdin 流し込み）があればそれで

## 既知リスク

- Windows の PATH に `omni-ai` が入らない Python 配置がある（`Scripts\` が PATH に無い）
- PowerShell と bash で引用符・改行の扱いが違う（スクリプトは `.ps1` を優先する規約）
- `httpx[http2]` の wheel が Python 3.14 で未提供の可能性 → 3.12 を使う

## おまかせで判断すること（事後報告）

- 検査台本は PowerShell（`.ps1`）で、`scratchpad` に置き結果を集める
- 不具合は票にして、重大なら本スプリント内で直し、軽微は別スプリントに託す
- Docker Sandbox は対象外（師匠の暗黙前提: 初回 clone 直後。別途入れる）

## 変更ログ

- v1 [2026-11-08T16:30+09:00]: 初版（師匠の指示と実機の初期状態を反映）
- v2 [2026-11-08T18:30+09:00]: 実行完了・close

## 実行結果（2026-11-08）

### 合格

- **T1** `py -3.12 -m pip install -e .` 通る（ログ: scratchpad/t1-install.log）
- **T2** `omni-ai --help` / `config path` / `doctor` 走行（ログ: scratchpad/t2-cli.log）
- **T3** 設定ファイルを直書きで作成成功。学び: PowerShell の `Set-Content -Encoding UTF8` は BOM 付き → tomllib が拒否するので `[System.IO.File]::WriteAllText` + `UTF8Encoding $false` で BOM 無し必須（docs/install.md 5 節に追記）
- **T4** `config validate` の誤り指摘は場所つき: providers 未知名 / unknown key / 構文エラー すべて場所つきで出る（ログ: scratchpad/t4-validate.log）
- **T5** `models catalog` / `list` 動作（ログ: scratchpad/t5-models.log）。`size` に不親切メッセージ発見 → バグ票 `bug-models-size-unknown-message`
- **T7** Ollama Chat 実機: generate ✓（qwen2.5:0.5b で content 返る）、stream ✓（11 イベント完走）。usage が None → バグ票 `bug-ollama-chat-usage-none`
- **T8** doctor 網羅性: Docker 未・Gemini キー欠落を場所つきで指摘（T2 のログに含まれる）
- **T9** `extras[search]` の `DDGSSearchAdapter` import 通る
- **T11** docs/install.md に Windows 節追記（BOM 注意 / API キー環境変数 / PATH / Python 3.14 の httpx[http2]）

### 未確認

- **T6** Gemini Chat: `GEMINI_API_KEY` が無いため実機確認スキップ

### バグ票（起票済み・直す役は別スプリント）

- `.pair-agent/bugs/models-size-unknown-message.md`: `omni-ai models size <ollama名>` のエラーメッセージが場所と直し方を示さない
- `.pair-agent/bugs/ollama-chat-usage-none.md`: Ollama Chat generate で usage が None（マッピング漏れの可能性）

### 判断ログ（事後報告・おまかせ）

- Gemini キー無しの validate ERROR は意図した挙動と判断（providers に宣言した以上キーが要るのは当然）。install.md に書くに留める
- `httpx[http2]` の Python 3.14 問題はビルド済みの 3.12 を使うことで回避
