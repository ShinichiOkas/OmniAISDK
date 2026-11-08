---
sprint_id: b04dd517-edd8-4bef-9a2a-a039f7640b27
version: 1
status: executing
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

## タスク

- [ ] Docker Desktop を起動し、イメージを pull、隔離の設定を実測
- [ ] DockerSandboxAdapter（provider_name "docker"）・テスト・live・ドキュメント

## おまかせで判断した事項（事後報告）

| # | 判断 | 理由 |
| :--- | :--- | :--- |

## 変更ログ

- v1 [2026-11-08]: 初版
