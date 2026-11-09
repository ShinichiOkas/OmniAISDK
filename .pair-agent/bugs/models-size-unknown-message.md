---
id: bug-models-size-unknown-message
created_at: 2026-11-08
status: open
severity: minor
domain: cli/models
reporter: AI (windows-smoke-test)
---

# `omni-ai models size <ollama名>` のエラーが場所と直し方を示さない

## 見たこと（実測・2026-11-08 Windows 11）

```
> omni-ai models size
a model name is required (see `omni-ai models catalog`)

> omni-ai models size qwen2.5:0.5b
qwen2.5:0.5b: unknown size
```

- `catalog` に載っている名前（例: `fastembed-embedding` / `whisper-base` / `ollama:<model>` / `docker:<image>`）しか受け付けないらしい
- しかし `qwen2.5:0.5b` を渡した時のエラーは `unknown size` だけで、**何が正しい名前か・どこで調べるか**が出ない
- `ollama:qwen2.5:0.5b` のように prefix が必要か、それとも他の形式かもメッセージから分からない

## 期待（提案）

- 「unknown size」の後に `see omni-ai models catalog`（または `expected formats: catalog name, ollama:<model>, docker:<image>`）を添える
- もしくは、名前が ollama / docker にありそうなら「もしかして `ollama:qwen2.5:0.5b`？」と推測を出す

## 再現手順

1. `omni-ai models size qwen2.5:0.5b`

## 環境

- Windows 11, Python 3.12.10, omni-ai-sdk 0.1.0
