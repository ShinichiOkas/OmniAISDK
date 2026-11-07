# ddgs fixtures

実物から記録（2026-11-08、師匠の Mac、ddgs 9.16.1、backend="auto"）。

実測で分かったこと:
- ddgs v9 は既定 (backend="auto") で複数のエンジン（bing・brave・duckduckgo・google・mojeek・yandex・wikipedia 等）を横断する。結果にどのエンジン由来かは含まれない
- 結果の形は `{"title", "href", "body"}`
- 意味の無いクエリでも auto では何かしら返る。backend="duckduckgo" だけにすると `DDGSException("No results found.")`
- 空クエリは `DDGSException("query is mandatory.")`
