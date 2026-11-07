"""独立レビュー（2026-11-07）で見つかった不具合の回帰テスト。番号はレビュー報告の番号。"""

import io
import json
import logging
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from omni_ai import (
    ChatMessage,
    MediaPart,
    OmniError,
    ProviderOverloadedError,
    ProviderTimeoutError,
    SchemaValidationError,
    StreamInterruptedError,
    ToolDefinition,
    omni_tool,
)
from omni_ai._http import RetryPolicy, SyncTransport
from omni_ai.providers.gemini import GeminiChatAdapter
from omni_ai.providers.ollama import OllamaChatAdapter
from omni_ai.router import FallbackChatAdapter, OmniClient
from omni_ai.tools import ToolRunner
from omni_ai.types import ToolCall
from tests.fakes import FakeChat, reply

OLLAMA = "http://ollama.test:11434"
GEMINI = "https://gemini.test/v1beta"
GEN = f"{GEMINI}/models/gemini-3.1-flash-lite:generateContent"
STREAM = f"{GEMINI}/models/gemini-3.1-flash-lite:streamGenerateContent"


class Person(BaseModel):
    name: str
    age: int


def ollama(**kw: object) -> OllamaChatAdapter:
    a = OllamaChatAdapter(
        base_url=OLLAMA, default_model="m", retry=RetryPolicy(max_attempts=2, base_delay=0.0), **kw
    )  # type: ignore[arg-type]
    a._sync._sleep = lambda _s: None
    return a


def gemini(**kw: object) -> GeminiChatAdapter:
    a = GeminiChatAdapter(
        api_key="k", base_url=GEMINI, retry=RetryPolicy(max_attempts=2, base_delay=0.0), **kw
    )  # type: ignore[arg-type]
    a._sync._sleep = lambda _s: None
    return a


def ndjson(*objs: dict) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


# 1 ---------------------------------------------------------------------------
@respx.mock
def test_01_ollama_stream_validates_schema_and_counts_attempts() -> None:
    body = ndjson(
        {"model": "m", "message": {"content": '{"name": "Ann", "age": 30}'}, "done": False},
        {"model": "m", "message": {"content": ""}, "done": True, "done_reason": "stop"},
    )
    respx.post(f"{OLLAMA}/api/chat").mock(
        side_effect=[httpx.Response(503, json={"error": "busy"}), httpx.Response(200, content=body)]
    )
    final = list(ollama().stream([ChatMessage.user("x")], response_schema=Person))[-1]
    assert final.response is not None
    assert final.response.parsed == Person(name="Ann", age=30)
    assert final.response.meta.attempts == 2


# 2 ---------------------------------------------------------------------------
@respx.mock
def test_02_stream_schema_failure_is_schema_error() -> None:
    sse = b"".join(
        b"data: " + json.dumps(c).encode() + b"\n\n"
        for c in [
            {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
            {"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "STOP"}]},
        ]
    )
    respx.post(STREAM).mock(return_value=httpx.Response(200, content=sse))
    with pytest.raises(SchemaValidationError):
        list(gemini().stream([ChatMessage.user("x")], response_schema=Person))


# 3 ---------------------------------------------------------------------------
def test_03_internal_calls_run_even_with_external_pending() -> None:
    @omni_tool
    def local(x: int) -> int:
        """Local."""
        return x * 2

    ext = ToolDefinition(name="external", parameters_schema={"type": "object", "properties": {}})
    chat = FakeChat(
        "gemini",
        [
            reply(
                calls=[
                    ToolCall(id="a", name="local", arguments={"x": 2}),
                    ToolCall(id="b", name="external", arguments={}),
                ]
            )
        ],
    )
    res = ToolRunner(chat).run([ChatMessage.user("q")], [local, ext])
    assert [c.id for c in res.pending_tool_calls] == ["b"]
    tool_msgs = [m for m in res.messages if m.role.value == "tool"]
    assert [(m.tool_call_id, m.content) for m in tool_msgs] == [("a", "4")]


# 4 ---------------------------------------------------------------------------
@respx.mock
def test_04_error_body_read_failure_is_mapped() -> None:
    class Boom(httpx.SyncByteStream):
        closed = False

        def __iter__(self):  # type: ignore[no-untyped-def]
            raise httpx.ReadTimeout("slow body")
            yield b""

        def close(self) -> None:
            Boom.closed = True

    respx.post("https://t.test/x").mock(return_value=httpx.Response(503, stream=Boom()))
    t = SyncTransport(
        provider="t",
        base_url="https://t.test",
        retry=RetryPolicy(max_attempts=1),
        error_mapper=lambda s, b, h: ProviderOverloadedError("x", provider="t"),
    )
    with pytest.raises(ProviderTimeoutError), t.stream("POST", "x"):
        pass
    assert Boom.closed


# 5 ---------------------------------------------------------------------------
@respx.mock
def test_05_malformed_ndjson_line_is_omni_error() -> None:
    body = ndjson({"model": "m", "message": {"content": "Hi"}, "done": False}) + b"<html>proxy error</html>\n"
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=httpx.Response(200, content=body))
    it = ollama().stream([ChatMessage.user("x")])
    assert next(it).delta_text == "Hi"
    with pytest.raises(StreamInterruptedError):
        next(it)


@respx.mock
def test_05_malformed_sse_is_omni_error() -> None:
    respx.post(STREAM).mock(return_value=httpx.Response(200, content=b"data: <html>\n\n"))
    with pytest.raises(OmniError):
        list(gemini().stream([ChatMessage.user("x")]))


# 6 + 7 ------------------------------------------------------------------------
def _mock_upload() -> respx.Route:
    respx.post("https://gemini.test/upload/v1beta/files").mock(
        return_value=httpx.Response(200, headers={"x-goog-upload-url": "https://gemini.test/s"}, json={})
    )
    return respx.post("https://gemini.test/s").mock(
        return_value=httpx.Response(
            200,
            json={"file": {"name": "files/f", "uri": "u", "mimeType": "application/pdf", "state": "ACTIVE"}},
        )
    )


@respx.mock
def test_06_07_upload_uses_remaining_length_rewinds_and_caches() -> None:
    fin = _mock_upload()
    gen = respx.post(GEN).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
        )
    )
    stream = io.BytesIO(b"HEADER" + b"x" * 1000)
    stream.read(6)
    msg = ChatMessage.user([MediaPart(type="document", source=stream, mime_type="application/pdf")])
    a = gemini(inline_limit=100)
    a.generate([msg])
    a.generate([msg])  # 同じ履歴をもう一度（ToolRunner の 2 ターン目など）
    assert fin.call_count == 1  # キャッシュでアップロードは 1 回
    req = fin.calls[0].request
    assert req.headers["content-length"] == "1000" and len(req.content) == 1000
    assert stream.tell() == 6  # 呼び出し側のストリーム位置を戻している
    assert gen.call_count == 2


@respx.mock
def test_07_inline_stream_can_be_sent_twice() -> None:
    route = respx.post(GEN).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
        )
    )
    msg = ChatMessage.user([MediaPart(type="image", source=io.BytesIO(b"abc"), mime_type="image/png")])
    a = gemini()
    a.generate([msg])
    a.generate([msg])
    datas = [
        json.loads(c.request.content)["contents"][0]["parts"][0]["inlineData"]["data"] for c in route.calls
    ]
    assert datas == ["YWJj", "YWJj"]


# 8 ---------------------------------------------------------------------------
async def test_08_sync_run_with_async_tool_inside_running_loop() -> None:
    @omni_tool
    async def aecho(text: str) -> str:
        """Echo."""
        return text

    chat = FakeChat(
        "x", [reply(calls=[ToolCall(id="a", name="aecho", arguments={"text": "hi"})]), reply("done")]
    )
    ToolRunner(chat).run([ChatMessage.user("q")], [aecho])  # このテスト自体がループ内で動いている
    assert chat.calls[1]["messages"][-1].content == "hi"


# 9 ---------------------------------------------------------------------------
async def test_09_parallel_raise_is_not_wrapped() -> None:
    @omni_tool
    async def bad(x: int) -> int:
        """Bad."""
        raise ValueError("nope")

    chat = FakeChat(
        "x",
        [
            reply(
                calls=[
                    ToolCall(id="a", name="bad", arguments={"x": 1}),
                    ToolCall(id="b", name="bad", arguments={"x": 2}),
                ]
            )
        ],
    )
    with pytest.raises(ValueError):
        await ToolRunner(chat, on_tool_error="raise").arun([ChatMessage.user("q")], [bad])


# 10 --------------------------------------------------------------------------
@respx.mock
def test_10_single_inline_limit(tmp_path: Path) -> None:
    fin = _mock_upload()
    respx.post(GEN).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
        )
    )
    f = tmp_path / "a.pdf"
    f.write_bytes(b"x" * 300)
    gemini(inline_limit=10_000).generate(
        [ChatMessage.user([MediaPart(type="document", source=f, mime_type="application/pdf")])],
        gemini={"inline_limit": 100},
    )
    assert fin.call_count == 1  # 名前空間側の上限で判定してアップロードした


# 11 --------------------------------------------------------------------------
async def test_11_close_blocks_async_use(caplog: pytest.LogCaptureFixture) -> None:
    a = ollama()
    _ = a._async.client  # 非同期クライアントを作っておく
    with caplog.at_level(logging.WARNING, logger="omni_ai"):
        a.close()
    assert "aclose" in caplog.text
    with pytest.raises(OmniError, match="closed"):
        await a.agenerate([ChatMessage.user("x")])
    await a.aclose()


# 12 --------------------------------------------------------------------------
async def test_12_abandoned_async_stream_closes_inner() -> None:
    closed: list[str] = []

    class Tracking(FakeChat):
        async def astream(self, messages, **kwargs):  # type: ignore[no-untyped-def,override]
            try:
                for i in range(100):
                    from omni_ai.types import StreamEvent

                    yield StreamEvent(type="text_delta", delta_text=str(i))
            finally:
                closed.append(self.name)

    ai = OmniClient(chat=FallbackChatAdapter([Tracking("p", [])]))
    gen = ai.chat.astream([ChatMessage.user("x")])
    async for _ev in gen:
        break
    await gen.aclose()  # type: ignore[attr-defined]
    assert closed == ["p"]
