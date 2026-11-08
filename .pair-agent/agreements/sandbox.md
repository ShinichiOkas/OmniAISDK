---
sprint_id: b04dd517-edd8-4bef-9a2a-a039f7640b27
version: 2
status: reviewing
created_at: 2026-11-08T07:00:00+09:00
updated_at: 2026-11-08T07:00:00+09:00
domain_tags: [python-sdk, sandbox, docker, security]
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

SandboxCapability を、ローカルの Docker で Python を隔離実行する形で実装する。

## 師匠の指示（原文 2026-11-08）

- 「では画像は保留。sandboxに移りましょう」
- 方式: 「ローカルの Docker (Recommended)」
- Docker Desktop: 「起動してよい (Recommended)」（Python の公式 slim イメージを pull。サイズは pull 前に確かめる）

## 事実（2026-11-08・Mac）

- Docker Desktop 4.64.0 が入っているが未起動。docker CLI は PATH に無い（/Applications/Docker.app/Contents/Resources/bin/docker）
- Mnemo・Ollama にコード実行の仕組みは無い

## 進捗（2026-11-08）

- Docker Desktop は新しいライセンスへの同意待ちで止まっていた（プロセス引数 --name=new-license、API は 500）。AI は同意せず、師匠に依頼 → 師匠（原文）「同意しました」
- その後 Docker Engine 29.9.2 が起動。python:3.12-slim を pull（圧縮 43.5MB・展開 205MB）
- CLI は PATH に無く、資格情報ヘルパー（docker-credential-desktop）も同じ bin にあるため、PATH に /Applications/Docker.app/Contents/Resources/bin を足す必要があった

## タスク

- [x] Docker Desktop を起動し、イメージを pull、隔離の設定を実測
- [x] DockerSandboxAdapter（provider_name "docker"）・テスト・live・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |
| X-01 | docker CLI を subprocess で使う（Engine API や docker SDK は使わない） | 3 OS 共通・依存を増やさない |
| X-02 | 隔離の既定を厳しめに（ネットワーク遮断・読み取り専用・非 root・資源制限） | 実測で全項目が効いた |
| X-03 | 時間切れは例外でなく timed_out=True の結果で返す | ToolRunner がモデルに結果を返せる |
| X-04 | 生成ファイルは印付きの tar で標準出力から取り出す | --rm と tmpfs ではあとから取り出せない |
| X-05 | 実行時にはイメージを取りに行かない（--pull never・ensure_image） | 予期しないダウンロードを避ける |
| X-06 | CI では Docker を使わず、subprocess を偽物にして単体テスト | macOS / Windows のランナーに Docker が無い |

## 変更ログ

- v1 [2026-11-08]: 初版
- v2 [2026-11-08]: 実装・live 確認（Ollama の CODE_EXECUTION を Docker で end-to-end）
