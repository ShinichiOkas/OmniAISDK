"""テスト用の偽 ChatCapability。応答や例外を台本どおりに返す。"""

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from omni_ai.capabilities import ChatCapability, SearchCapability
from omni_ai.types import (
    BuiltinTool,
    ChatMessage,
    ChatResponse,
    ResponseMeta,
    SearchItem,
    SearchResponse,
    StreamEvent,
    ToolCall,
    Usage,
)


def reply(
    text: str | None = None,
    *,
    calls: list[ToolCall] | None = None,
    provider: str = "fake",
    model: str = "m",
    usage: Usage | None = None,
) -> ChatResponse:
    return ChatResponse(
        content=text,
        tool_calls=calls or [],
        finish_reason="tool_calls" if calls else "stop",
        meta=ResponseMeta(provider=provider, model=model, usage=usage or Usage()),
    )


class FakeChat(ChatCapability):
    def __init__(
        self,
        name: str,
        script: list[Any],
        *,
        builtins: frozenset[BuiltinTool] = frozenset(),
        default_model: str | None = "m",
    ) -> None:
        self.name = name
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.supported_builtins = builtins
        self.default_model = default_model
        self.closed = False

    @property
    def provider_name(self) -> str:  # type: ignore[override]
        return self.name

    def _next(self, messages: Sequence[ChatMessage], kwargs: dict[str, Any]) -> Any:
        self.calls.append({"messages": list(messages), **kwargs})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def generate(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        res = self._next(messages, kwargs)
        res.meta.provider = self.name
        return res

    async def agenerate(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return self.generate(messages, **kwargs)

    def stream(self, messages: Sequence[ChatMessage], **kwargs: Any) -> Iterator[StreamEvent]:
        item = self._next(messages, kwargs)
        for ev in item:
            if isinstance(ev, BaseException):
                raise ev
            if ev.response is not None:
                ev.response.meta.provider = self.name
            yield ev

    async def astream(self, messages: Sequence[ChatMessage], **kwargs: Any) -> AsyncIterator[StreamEvent]:
        for ev in self.stream(messages, **kwargs):
            yield ev

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


def stream_of(text: str, provider: str = "fake") -> list[StreamEvent]:
    res = reply(text, provider=provider)
    return [
        StreamEvent(type="text_delta", delta_text=text),
        StreamEvent(type="message_complete", finish_reason="stop", response=res, meta=res.meta),
    ]


class FakeSearch(SearchCapability):
    provider_name = "fakesearch"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def _res(self, query: str) -> SearchResponse:
        self.queries.append(query)
        return SearchResponse(
            results=[SearchItem(title="T", url="https://e.x", content=f"about {query}")],
            meta=ResponseMeta(provider="fakesearch", model="-"),
        )

    def search(self, query: str, **kw: Any) -> SearchResponse:
        return self._res(query)

    async def asearch(self, query: str, **kw: Any) -> SearchResponse:
        return self._res(query)
