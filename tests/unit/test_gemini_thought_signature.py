"""Gemini の thought signature 周回（round-trip）が壊れていないことを確かめる。

応答の parts を provider_state に入れ、次のリクエストの assistant content に組み立て直したとき、
thoughtSignature が保たれていること。provider_state が無いときはダミーの署名が付くこと。
"""

from __future__ import annotations

from omni_ai.providers.gemini.codec import (
    DUMMY_THOUGHT_SIGNATURE,
    PROVIDER,
    build_contents,
    parse_response,
)
from omni_ai.types import ChatMessage, Role, ToolCall


def _make_request():
    # 直接 ChatRequest を組み立てなくても、parse_response は dict だけ受けるので、
    # 偽の req を 1 本用意する
    class _Req:
        model = "gemini-3.1-flash-lite"
        options = {}

    return _Req()


def test_parse_response_captures_parts_including_thought_signature():
    data = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"text": "thinking...", "thought": True, "thoughtSignature": "sig-xyz"},
                        {"functionCall": {"name": "get_weather", "args": {"city": "Tokyo"}, "id": "c1"}},
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
        "modelVersion": "gemini-3.1-flash-lite",
        "responseId": "r-1",
    }
    response = parse_response(data, _make_request())
    assert response.provider_state.get(PROVIDER) is not None
    stored_parts = response.provider_state[PROVIDER]["content"]["parts"]
    # 元の parts がそのまま入っている（thoughtSignature が落ちない）
    assert stored_parts[0].get("thoughtSignature") == "sig-xyz"
    assert stored_parts[1]["functionCall"]["name"] == "get_weather"


def test_build_contents_uses_stored_parts_for_assistant_message():
    # 想定: 直前の応答の parts を provider_state に持った assistant メッセージを次のリクエストに積む
    stored_parts = [
        {"text": "thinking...", "thought": True, "thoughtSignature": "sig-xyz"},
        {"functionCall": {"name": "get_weather", "args": {"city": "Tokyo"}, "id": "c1"}},
    ]
    assistant_msg = ChatMessage(
        role=Role.ASSISTANT,
        content="",
        tool_calls=[ToolCall(id="c1", name="get_weather", arguments={"city": "Tokyo"})],
        provider_state={PROVIDER: {"content": {"role": "model", "parts": stored_parts}}},
    )
    user_msg = ChatMessage.user("weather?")
    _, contents = build_contents([user_msg, assistant_msg], inline_limit=10_000_000)
    # provider_state の parts がそのまま出ている（thoughtSignature が保たれる）
    assistant_content = contents[-1]
    assert assistant_content["role"] == "model"
    assert assistant_content["parts"] == stored_parts


def test_build_contents_adds_dummy_signature_when_no_provider_state():
    # provider_state が無い assistant の tool_calls → 先頭に DUMMY_THOUGHT_SIGNATURE が付く
    assistant_msg = ChatMessage(
        role=Role.ASSISTANT,
        content="",
        tool_calls=[
            ToolCall(id="c1", name="get_weather", arguments={"city": "Tokyo"}),
            ToolCall(id="c2", name="get_weather", arguments={"city": "Osaka"}),
        ],
    )
    user_msg = ChatMessage.user("weather?")
    _, contents = build_contents([user_msg, assistant_msg], inline_limit=10_000_000)
    parts = contents[-1]["parts"]
    # 先頭 functionCall にだけダミー署名が付く（2 本目には付かない）
    fc_parts = [p for p in parts if "functionCall" in p]
    assert len(fc_parts) == 2
    assert fc_parts[0].get("thoughtSignature") == DUMMY_THOUGHT_SIGNATURE
    assert "thoughtSignature" not in fc_parts[1]


def test_round_trip_response_to_next_request_preserves_signature():
    """応答 → 次のリクエストへ組み戻したときに、thoughtSignature が保たれる。"""
    data = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"functionCall": {"name": "f", "args": {}, "id": "c1"},
                         "thoughtSignature": "abc"}
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {},
        "modelVersion": "gemini-3.1-flash-lite",
    }
    response = parse_response(data, _make_request())
    assistant = response.to_message()
    _, contents = build_contents([ChatMessage.user("q"), assistant], inline_limit=10_000_000)
    out_parts = contents[-1]["parts"]
    assert out_parts[0].get("thoughtSignature") == "abc"
