---
sprint_id: 875db2c1-3ec1-4d6f-90db-1d0cae6dab37
version: 1
status: executing
size: M
created_at: 2026-11-15T16:03:08+09:00
updated_at: 2026-11-15T17:00:00+09:00
domain_tags: [python-sdk, chat, providers, local, mlx, macos]
---

# スプリントゴール

ローカルプロバイダー（Chat）に mlx-lm を加える。Mac（Apple Silicon）で動作しているときに限る。

## 師匠の指示（原文 2026-11-15）

- 「ローカルプロバイダーに、mlx-mlを追加して欲しい。もちろんMacで動作しているとき限定」

## 師匠の決定（選択肢への回答 2026-11-15）

- 対象: 「mlx-lm で合っている」 → provider_name は `"mlx-lm"`（エンジン名ルール）
- 呼び出し方: 「プロセス内で直接」 → extras で mlx-lm を入れ、Python API で読み込んで生成。サーバー不要
- 範囲: 「Chat（生成・ストリーム）」のみ。Embedding・Audio は対象外
- サイズ: 「M」

## 判断（案。師匠の確認待ち）

- J-M1: 名前 — provider_name `"mlx-lm"`、クラス `MLXLMChatAdapter`、パッケージ `omni_ai.providers.mlx_lm`
- J-M2: 依存 — extras `[mlx]` = `mlx-lm`（環境マーカー `sys_platform == 'darwin' and platform_machine == 'arm64'`）。他の OS ではインストールしても何も入らない
- J-M3: Mac 以外での振る舞い — アダプター生成時に `UnsupportedFeatureError`（「mlx-lm は Apple Silicon の macOS 専用」）。Mac で extras 未導入なら `pip install 'omni-ai[mlx]'` を案内
- J-M4: 構造 — HTTP 前提の `BaseChatAdapter` は継がず、`ChatCapability` を直接実装。モデルは初回利用時に 1 度だけ読み込む（fastembed の LazyModel と同じ考え方・スレッド安全）。生成は 1 本ずつ直列化（MLX の GPU 実行は並行に向かない）。async は別スレッドで回す
- J-M5: モデル — 既定モデルは持たない（J-02 に従い `models` 指定必須）。モデル ID は Hugging Face のリポジトリ名（例 `mlx-community/Qwen3-4B-4bit`）またはローカルパス。ダウンロードは mlx-lm の既定（HF キャッシュ）に任せる。別モデルを指定されたら前のモデルを解放して読み替える（同時に保持するのは 1 本）
- J-M6: 対応する機能 — テキストの生成・ストリーム・temperature・max_tokens・usage（トークン数）・finish_reason（stop / length）。会話はモデルの chat template で組み立てる
- J-M7: 今回は対応しない機能（明示エラー `UnsupportedFeatureError`） — tools（ツール呼び出し）、response_schema（構造化出力。mlx-lm に制約付き生成が無い）、画像入力（mlx-vlm は別ライブラリ）
  - **師匠の決定（原文 2026-11-15）**: 「ツール呼び出しや号増加出力、画像入力は実用上は必要なので、次ステップでは必ず実装する前提で進めて」（「号増加出力」＝構造化出力）
  - → 「見送り」ではなく**次スプリントで必ず実装する**。今回の設計は、この 3 つを後から足せる形にしておく（メッセージ変換・生成ループ・モデル読み込みを差し替え可能に分ける。画像は mlx-vlm 側の読み込みに切り替えられるようにする）
  - 次スプリントの票: `.pair-agent/requests/2026-11-15-1610-mlx-lm-tools-schema-vision.md`
- J-M8: timeout — トークン間で締め切りを確かめ、超えたら打ち切って `ProviderTimeoutError`
- J-M9: 試験 — 偽のモデル／トークナイザーでの契約テスト（CI・全 OS）＋この Mac での実物テスト。実応答を記録して fixtures にする（Skill: record-real-responses-before-trusting-docs）
  - 実物モデル: **手元の `~/work/LLMsetup/models/google_gemma-4-26B-A4B-it-MLX-mxfp4-g32`（13GB）だけ**。ダウンロードはしない（師匠の決定 2026-11-15「手元の Gemma 4 だけ」）
  - この Mac は M1 / 16GB。まず読み込めるかを 1 回だけ試し、メモリ不足ならそこで止めて相談する
  - 実物テストはモデルのパスを環境変数で渡したときだけ走る（パスを試験コードに固定しない）
  - 参考: LLMsetup の .venv は mlx-lm 0.31.3 / mlx 0.32.4（mxfp4 を変換できた版）
- J-M10: CLI（setup メニュー・models） — Chat の選択肢の末尾に `mlx-lm` を追加。Apple Silicon の Mac でだけ表示
- J-M11: 設計書 — `docs/OmniAI SDK 実装設計書 (コア＋Chat).md` に節を追加
- J-M12: git — コミットして main へ push（師匠の決定 2026-11-15）
- J-M13: 思考モード — **モデルの既定に従う**（師匠の決定 2026-11-15「モデルの既定に従う」）。SDK は指定が無ければ何も渡さない（mlx-lm の既定＝ Gemma 4 では ON）。呼び出しごとに `provider_options={"mlx-lm": {"think": bool}}` で切り替え。思考は content から分けて `reasoning` / `reasoning_delta` に入れる（区切りは tokenizer の `think_start` / `think_end`）

## 探針の結果（2026-11-15・この Mac = M1 / 16GB、LLMsetup の .venv: mlx-lm 0.31.3 / mlx 0.32.4）

- 読み込み: **成功**（16.9 秒）
- 生成: **失敗**。`RuntimeError: [METAL] Command buffer execution failed: Insufficient Memory (00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)`
  - 直前の警告: 「Generating with a model that requires 12790 MB which is close to the maximum recommended size of 12124 MB」
  - モデルの必要量 12,790MB が、GPU に割り当てられる推奨上限 12,124MB を超えている
- ついでに分かったこと: このモデルの chat template は、既定で system ターンに `<|think|>` を入れる（思考モードが既定で有効）
- 設計への反映: Metal のメモリ不足は、意味の分かるエラーに変換して返す（生の RuntimeError を漏らさない）
- **師匠の決定（2026-11-15）**: 「小さい Gemma 4 を用意」→「e2b を DL→LLMsetup/models」
  - 実物モデルを `mlx-community/gemma-4-e2b-it-4bit`（3.6GB）に変更し、`~/work/LLMsetup/models/mlx-community_gemma-4-e2b-it-4bit` に置く（J-M9 の 26B-A4B を置き換え）
  - mlx-vlm 向けの変換（image-text-to-text）なので、mlx-lm でテキスト生成できるかを先に確かめる
  - ディスクの空きは DL 前で 11GB（使用率 98%）

## 探針 2（2026-11-15・この Mac = M1 / 16GB、OmniAISDK の .venv: mlx-lm 0.32.2 / mlx 0.32.4、モデル gemma-4-e2b-it-4bit）

- 読み込み 3.0 秒、生成 約 33 トークン/秒、ピークメモリ 2.7GB。mlx-vlm 向けの変換でも mlx-lm でテキスト生成できた
- **思考モードは既定で ON**。mlx-lm の `TokenizerWrapper.apply_chat_template` が、指定が無ければ `enable_thinking = has_thinking` を入れる（このモデルでは True）
  - ON: 「日本の首都は？」に 118 トークン・6.3 秒。OFF（`enable_thinking=False`）: 「1+1は？」に 10 トークン・0.4 秒
- 思考は本文の中に `<|channel>thought\n … <channel|>` で囲まれて出てくる（そのままでは content に混ざる）
  - mlx-lm の tokenizer が `think_start` / `think_end` の文字列を持っている（このモデルでは `<|channel>thought` / `<channel|>`）→ モデルごとに書き分けずに reasoning と content に分けられる
- 次スプリント向けのメモ: tokenizer に `has_tool_calling` / `tool_call_start` / `tool_call_end` / `tool_parser` がある

## 懸念

- 師匠の開発機は Windows。この Mac で作ったものは Windows の CI では「偽物テスト」しか通らない。実物確認はこの Mac でだけ可能
- 実物テストでモデルをダウンロードする（0.6B 4bit で約 0.4GB）
