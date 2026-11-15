"""MLXLMChatAdapter の契約テスト。応答は実物（gemma-4-e2b-it-4bit）の記録（fixtures/mlx_lm）を再生する。

mlx-lm 本体は使わないので、どの OS の CI でも走る。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from omni_ai import (
    ChatMessage,
    InvalidRequestError,
    NotFoundError,
    OmniError,
    ProviderTimeoutError,
    StreamInterruptedError,
    UnsupportedFeatureError,
    omni_tool,
)
from omni_ai.providers.mlx_lm import GenChunk, MLXLMChatAdapter, ThinkSplitter
from omni_ai.types import MediaPart, TextPart

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "mlx_lm"
MODEL = "mlx-community/gemma-4-e2b-it-4bit"


def rec(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class ReplayBackend:
    """記録したプロンプトと区切りの列をそのまま返す偽の実行部。"""

    def __init__(
        self,
        record: dict[str, Any] | None = None,
        *,
        fail: BaseException | None = None,
        fail_load: BaseException | None = None,
        fail_after: int | None = None,
    ) -> None:
        self.record = record or rec("think_off")
        self.fail = fail
        self.fail_load = fail_load
        self.fail_after = fail_after
        self.loads: list[str] = []
        self.rendered: list[tuple[list[dict[str, Any]], bool | None]] = []
        self.generated: list[dict[str, Any]] = []
        self.releases = 0

    def load(self, model: str) -> tuple[Any, Any]:
        if self.fail_load is not None:
            raise self.fail_load
        self.loads.append(model)
        return object(), object()

    def render(self, tokenizer: Any, messages: list[dict[str, Any]], think: bool | None) -> str:
        self.rendered.append((messages, think))
        return str(self.record["prompt"])

    def think_markers(self, tokenizer: Any) -> tuple[str, str] | None:
        m = self.record["think_markers"]
        return (m[0], m[1]) if m else None

    def generate(
        self,
        model: Any,
        tokenizer: Any,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float | None,
        sampler_options: dict[str, Any],
    ) -> Iterator[GenChunk]:
        self.generated.append(
            {"max_tokens": max_tokens, "temperature": temperature, "sampler": sampler_options}
        )
        for i, c in enumerate(self.record["chunks"]):
            if self.fail_after is not None and i == self.fail_after:
                assert self.fail is not None
                raise self.fail
            yield GenChunk(**c)
        if self.fail is not None and self.fail_after is None:
            raise self.fail

    def release(self) -> None:
        self.releases += 1


def adapter(backend: ReplayBackend | None = None, **kw: Any) -> MLXLMChatAdapter:
    return MLXLMChatAdapter(default_model=MODEL, backend=backend or ReplayBackend(), **kw)


Q = [ChatMessage.user("日本の首都はどこ？一言で。")]


# ----------------------------------------------------------------------------- 生成


def test_text_without_thinking() -> None:
    b = ReplayBackend(rec("think_off"))
    res = adapter(b).generate(Q, **{"mlx-lm": {"think": False}})
    assert res.content == "東京"
    assert res.reasoning is None
    assert res.finish_reason == "stop"
    assert res.meta.provider == "mlx-lm" and res.meta.model == MODEL
    assert res.meta.estimated_cost_usd == 0.0
    last = rec("think_off")["chunks"][-1]
    assert res.meta.usage.prompt_tokens == last["prompt_tokens"]
    assert res.meta.usage.completion_tokens == last["generation_tokens"]
    assert res.meta.usage.total_tokens == last["prompt_tokens"] + last["generation_tokens"]
    assert b.rendered[0] == ([{"role": "user", "content": "日本の首都はどこ？一言で。"}], False)


def test_thinking_is_split_into_reasoning() -> None:
    res = adapter(ReplayBackend(rec("think_default"))).generate(Q)
    assert res.content == "東京"
    assert res.reasoning and res.reasoning.startswith("Thinking Process:")
    assert "<|channel>" not in (res.reasoning + res.content)
    assert "<channel|>" not in (res.reasoning + res.content)


def test_think_is_not_sent_unless_given() -> None:
    """思考モードはモデルの既定に従う（指定が無ければ何も渡さない）。"""
    b = ReplayBackend(rec("think_default"))
    adapter(b).generate(Q)
    assert b.rendered[0][1] is None
    adapter(b).generate(Q, **{"mlx-lm": {"think": True}})
    assert b.rendered[1][1] is True


def test_multi_turn_with_system() -> None:
    b = ReplayBackend(rec("system_multi_turn"))
    msgs = [
        ChatMessage.system("あなたは簡潔に答える。"),
        ChatMessage.user("1+1は？"),
        ChatMessage.assistant("2"),
        ChatMessage.user("それに3を足すと？"),
    ]
    res = adapter(b).generate(msgs)
    assert res.content == "5"
    assert [m["role"] for m in b.rendered[0][0]] == ["system", "user", "assistant", "user"]


def test_length_finish_reason_and_max_tokens() -> None:
    b = ReplayBackend(rec("length"))
    res = adapter(b).generate(Q, max_tokens=8)
    assert res.finish_reason == "length"
    assert b.generated[0]["max_tokens"] == 8


def test_default_max_tokens_and_sampler_options() -> None:
    b = ReplayBackend()
    adapter(b, default_max_tokens=123).generate(Q, temperature=0.7, **{"mlx-lm": {"top_p": 0.9, "top_k": 40}})
    assert b.generated[0] == {"max_tokens": 123, "temperature": 0.7, "sampler": {"top_p": 0.9, "top_k": 40}}


def test_text_parts_are_joined() -> None:
    b = ReplayBackend()
    adapter(b).generate([ChatMessage.user([TextPart(text="a"), TextPart(text="b")])])
    assert b.rendered[0][0][0]["content"] == "a\nb"


# ----------------------------------------------------------------------------- ストリーム


def test_stream_events_match_generate() -> None:
    evs = list(adapter(ReplayBackend(rec("think_default"))).stream(Q))
    kinds = [e.type for e in evs]
    assert kinds[-1] == "message_complete"
    assert "reasoning_delta" in kinds and "text_delta" in kinds
    # 思考が先、本文が後
    assert kinds.index("reasoning_delta") < kinds.index("text_delta")
    text = "".join(e.delta_text for e in evs if e.type == "text_delta")
    reasoning = "".join(e.delta_text for e in evs if e.type == "reasoning_delta")
    final = evs[-1].response
    assert final is not None and final.content == text == "東京" and final.reasoning == reasoning
    assert evs[-1].meta is not None and evs[-1].meta.time_to_first_event_ms is not None


async def test_async_generate_and_stream() -> None:
    a = adapter(ReplayBackend(rec("think_default")))
    res = await a.agenerate(Q)
    assert res.content == "東京"
    evs = [e async for e in a.astream(Q)]
    assert evs[-1].type == "message_complete" and evs[-1].response is not None
    assert evs[-1].response.content == "東京"


async def test_astream_early_exit_stops_generation_and_releases_lock() -> None:
    a = adapter(ReplayBackend(rec("think_default")))
    agen = a.astream(Q)
    async for _ in agen:
        break
    await agen.aclose()  # type: ignore[attr-defined]
    # ロックが解放されていれば次の呼び出しが通る
    assert (await a.agenerate(Q)).content == "東京"


# ----------------------------------------------------------------------------- 思考の切り分け
# 区切りの文字列が分割されて届いても扱える


@pytest.mark.parametrize("step", [1, 2, 3, 7])
def test_splitter_handles_markers_split_across_chunks(step: int) -> None:
    text = "<|channel>thought\nthink A<channel|>答え"
    s = ThinkSplitter(("<|channel>thought", "<channel|>"))
    out = []
    for i in range(0, len(text), step):
        out += s.feed(text[i : i + step])
    out += s.finish()
    reasoning = "".join(t for k, t in out if k == "reasoning")
    content = "".join(t for k, t in out if k == "content")
    assert (reasoning, content) == ("think A", "答え")


def test_splitter_starts_inside_thinking_when_prompt_opened_it() -> None:
    s = ThinkSplitter(("<think>", "</think>"), start_in_thinking=True)
    out = s.feed("ponder</think>\n\nanswer") + s.finish()
    assert out == [("reasoning", "ponder"), ("content", "answer")]


def test_splitter_without_markers_passes_text_through() -> None:
    s = ThinkSplitter(None)
    assert s.feed("<a") + s.feed("b>") + s.finish() == [("content", "<a"), ("content", "b>")]


def test_prompt_ending_with_think_start_begins_in_reasoning() -> None:
    r = {
        "prompt": "...<think>\n",
        "think_markers": ["<think>", "</think>"],
        "chunks": [{"text": "hmm"}, {"text": "</think>"}, {"text": "ok", "finish_reason": "stop"}],
    }
    res = adapter(ReplayBackend(r)).generate(Q)
    assert (res.reasoning, res.content) == ("hmm", "ok")


# ----------------------------------------------------------------------------- 未対応（次の段で実装）


@omni_tool
def get_weather(city: str) -> str:
    """Get current weather for a city"""
    return "sunny"


class Answer(BaseModel):
    city: str


def test_tools_are_unsupported_for_now() -> None:
    with pytest.raises(UnsupportedFeatureError):
        adapter().generate(Q, tools=[get_weather])


def test_response_schema_is_unsupported_for_now() -> None:
    with pytest.raises(UnsupportedFeatureError):
        adapter().generate(Q, response_schema=Answer)


def test_image_input_is_unsupported_for_now() -> None:
    msg = ChatMessage.user(
        [TextPart(text="what?"), MediaPart(type="image", source=b"\x89PNG", mime_type="image/png")]
    )
    with pytest.raises(UnsupportedFeatureError):
        adapter().generate([msg])


def test_tool_messages_are_unsupported_for_now() -> None:
    with pytest.raises(UnsupportedFeatureError):
        adapter().generate([*Q, ChatMessage.tool("sunny", tool_call_id="c1", name="get_weather")])


# ----------------------------------------------------------------------------- 要求の検査


def test_model_is_required() -> None:
    with pytest.raises(InvalidRequestError, match="model is required"):
        MLXLMChatAdapter(backend=ReplayBackend()).generate(Q)


def test_unknown_option_is_rejected_not_dropped() -> None:
    with pytest.raises(InvalidRequestError, match="unknown mlx-lm option"):
        adapter().generate(Q, **{"mlx-lm": {"thinking": True}})


def test_think_must_be_bool() -> None:
    with pytest.raises(InvalidRequestError, match="bool"):
        adapter().generate(Q, **{"mlx-lm": {"think": "yes"}})


def test_empty_messages_rejected() -> None:
    with pytest.raises(InvalidRequestError):
        adapter().generate([])


# ----------------------------------------------------------------------------- モデルの読み込み


def test_model_loaded_once_and_switched_on_demand() -> None:
    b = ReplayBackend()
    a = adapter(b)
    a.generate(Q)
    a.generate(Q)
    assert b.loads == [MODEL] and b.releases == 0
    a.generate(Q, model="other/model")
    assert b.loads == [MODEL, "other/model"] and b.releases == 1
    assert a.loaded_model == "other/model"


def test_tilde_path_is_expanded() -> None:
    b = ReplayBackend()
    adapter(b).generate(Q, model="~/models/x")
    assert b.loads == [str(Path("~/models/x").expanduser())]


def test_concurrent_calls_are_serialized() -> None:
    b = ReplayBackend(rec("think_default"))
    a = adapter(b)
    results: list[str | None] = []

    def run() -> None:
        results.append(a.generate(Q).content)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["東京"] * 4 and b.loads == [MODEL]


def test_close_releases_and_rejects_further_calls() -> None:
    b = ReplayBackend()
    a = adapter(b)
    a.generate(Q)
    a.close()
    assert b.releases == 1 and a.loaded_model is None
    with pytest.raises(OmniError, match="closed"):
        a.generate(Q)


# ----------------------------------------------------------------------------- エラーの対応づけ


def test_missing_local_model_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        adapter(ReplayBackend(fail_load=FileNotFoundError("no such dir"))).generate(Q)


METAL_OOM = RuntimeError(
    "[METAL] Command buffer execution failed: Insufficient Memory "
    "(00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)"
)


def test_metal_oom_before_output_is_a_clear_error() -> None:
    """2026-11-15 の探針で実際に出た文言（gemma-4-26B-A4B mxfp4 を M1 / 16GB で生成）。"""
    with pytest.raises(OmniError, match="not enough GPU memory") as ei:
        adapter(ReplayBackend(fail=METAL_OOM, fail_after=0)).generate(Q)
    assert not isinstance(ei.value, StreamInterruptedError)
    assert ei.value.raw_error is METAL_OOM


def test_failure_after_partial_output_is_stream_interrupted() -> None:
    b = ReplayBackend(rec("think_default"), fail=METAL_OOM, fail_after=30)
    with pytest.raises(StreamInterruptedError):
        list(adapter(b).stream(Q))


def test_timeout_between_tokens() -> None:
    with pytest.raises((ProviderTimeoutError, StreamInterruptedError)):
        adapter(ReplayBackend(rec("think_default"))).generate(Q, timeout=-1)


# ----------------------------------------------------------------------------- 機械の制限


def test_non_mac_is_refused_without_injected_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import omni_ai.providers.mlx_lm as mod

    monkeypatch.setattr(mod, "is_supported_platform", lambda: False)
    with pytest.raises(UnsupportedFeatureError, match="Apple Silicon"):
        MLXLMChatAdapter(default_model=MODEL)
