import io
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from omni_ai.types import (
    BuiltinTool,
    ChatMessage,
    ChatResponse,
    MediaPart,
    ResponseMeta,
    Role,
    TextPart,
    ToolCall,
    UrlRef,
    normalize_json_schema,
    omni_tool,
)


def test_media_source_rejects_bare_str() -> None:
    with pytest.raises(ValidationError, match="ambiguous"):
        MediaPart(type="image", source="photo.png", mime_type="image/png")


@pytest.mark.parametrize(
    "source",
    [b"\x89PNG", Path("x.png"), io.BytesIO(b"abc"), UrlRef(url="https://example.com/a.png")],
)
def test_media_source_accepts_supported_kinds(source: object) -> None:
    part = MediaPart(type="image", source=source, mime_type="image/png")
    assert part.source is source or part.source == source


def test_content_parts_discriminated() -> None:
    msg = ChatMessage.model_validate(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image", "source": b"x", "mime_type": "image/png"},
            ],
        }
    )
    assert isinstance(msg.content, list)
    assert isinstance(msg.content[0], TextPart)
    assert isinstance(msg.content[1], MediaPart)
    assert msg.text() == "hi"


def test_to_message_keeps_tool_calls_and_state() -> None:
    res = ChatResponse(
        content=None,
        tool_calls=[ToolCall(id="c1", name="f", arguments={"a": 1}, provider_state={"gemini": {"sig": "s"}})],
        finish_reason="tool_calls",
        meta=ResponseMeta(provider="gemini", model="m"),
        provider_state={"gemini": {"parts": [1]}},
    )
    msg = res.to_message()
    assert msg.role is Role.ASSISTANT
    assert msg.tool_calls and msg.tool_calls[0].provider_state == {"gemini": {"sig": "s"}}
    assert msg.provider_state == {"gemini": {"parts": [1]}}
    msg.tool_calls[0].arguments["a"] = 2
    assert res.tool_calls[0].arguments["a"] == 1  # deep copy


def test_omni_tool_keeps_function_callable_and_schema() -> None:
    @omni_tool
    def calculate_roi(initial_cost: float, annual_savings: float = 1.0) -> float:
        """投資回収年数を計算する。

        Args:
            initial_cost: 初期費用（円）
            annual_savings: 年間削減額（円）
        """
        return round(initial_cost / annual_savings, 2)

    assert calculate_roi(100.0, 50.0) == 2.0
    assert calculate_roi.name == "calculate_roi"
    assert calculate_roi.description == "投資回収年数を計算する。"
    schema = calculate_roi.parameters_schema
    assert "title" not in schema
    assert schema["required"] == ["initial_cost"]
    assert schema["properties"]["initial_cost"]["description"] == "初期費用（円）"
    assert calculate_roi.invoke({"initial_cost": "100", "annual_savings": 50}) == 2.0


def test_omni_tool_with_options_and_async() -> None:
    @omni_tool(name="lookup", description="d")
    async def _f(q: str) -> str:
        return q

    assert _f.name == "lookup" and _f.description == "d" and _f.is_async
    with pytest.raises(TypeError):
        _f.invoke({"q": "x"})


async def test_omni_tool_ainvoke_sync_and_async() -> None:
    @omni_tool
    def add(a: int, b: int) -> int:
        return a + b

    @omni_tool
    async def mul(a: int, b: int) -> int:
        return a * b

    assert await add.ainvoke({"a": 1, "b": 2}) == 3
    assert await mul.ainvoke({"a": 3, "b": 2}) == 6
    with pytest.raises(ValidationError):
        await add.ainvoke({"a": "not-int", "b": 2})


def test_omni_tool_rejects_varargs() -> None:
    with pytest.raises(TypeError):
        omni_tool(lambda *a: a)


def test_normalize_json_schema_inlines_defs_and_keeps_property_named_title() -> None:
    class Inner(BaseModel):
        title: str

    class Outer(BaseModel):
        inner: Inner
        items: list[Inner]

    out = normalize_json_schema(Outer.model_json_schema())
    assert "$defs" not in out and "title" not in out
    assert out["properties"]["inner"]["properties"]["title"] == {"type": "string"}
    assert out["properties"]["items"]["items"]["required"] == ["title"]


def test_normalize_json_schema_recursive_ref_degrades() -> None:
    class Node(BaseModel):
        child: "Node | None" = None

    out = normalize_json_schema(Node.model_json_schema())
    assert "$ref" not in str(out)


def test_builtin_tool_values() -> None:
    assert BuiltinTool.WEB_SEARCH.value == "builtin::web_search"
