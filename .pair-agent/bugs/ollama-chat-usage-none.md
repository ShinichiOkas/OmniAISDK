---
id: bug-ollama-chat-usage-none
created_at: 2026-11-08
status: open
severity: minor
domain: providers/ollama
reporter: AI (windows-smoke-test)
---

# Ollama Chat の generate で usage が None

## 見たこと（実測・2026-11-08 Windows 11, Ollama 0.40.12）

```python
res = client.chat.generate(
    [ChatMessage(role='user', content='reply with exactly: hello from ollama')],
    model='qwen2.5:0.5b',
)
# content: "Hello! I'm Qwen, ..."
# finish_reason: stop
# usage: None
```

- 内容と終了理由は返るが `usage` が None
- Ollama の `/api/chat` は `prompt_eval_count` / `eval_count` / `total_duration` などを返すので、`UsageInfo(input_tokens=prompt_eval_count, output_tokens=eval_count)` にマップできるはず

## 期待

- Ollama の応答から usage を埋めて返す

## 再現手順

1. ollama 0.40.12 ローカル稼働、qwen2.5:0.5b 導入済み
2. 上のコードを実行

## 環境

- Windows 11, Python 3.12.10, omni-ai-sdk 0.1.0, ollama 0.40.12
