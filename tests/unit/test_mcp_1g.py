"""1.G: MCPClientAdapter の transport 選択・auth・elicitation/notification 差し込み口の単体試験。

本物のサーバー接続は tests/contract/test_mcp.py の方でやる。ここでは構築時の分岐と、
包み（elicitation / notification）が意図どおり呼ばれるかを確かめる。
"""

from __future__ import annotations

from typing import ClassVar

import httpx
import pytest

from omni_ai.providers.mcp import MCPClientAdapter

# ---------- transport の選択 ----------


def test_transport_default_stdio_for_command() -> None:
    ad = MCPClientAdapter("x", command=["python", "-V"])
    assert ad.transport == "stdio"


def test_transport_default_streamable_http_for_url() -> None:
    ad = MCPClientAdapter("x", url="https://example.com/mcp")
    assert ad.transport == "streamable-http"


def test_transport_sse_explicit() -> None:
    ad = MCPClientAdapter("x", url="https://example.com/sse", transport="sse")
    assert ad.transport == "sse"


def test_transport_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="transport='stdio' requires command"):
        MCPClientAdapter("x", url="https://example.com", transport="stdio")
    with pytest.raises(ValueError, match="requires url"):
        MCPClientAdapter("x", command=["python"], transport="streamable-http")


def test_exactly_one_of_command_or_url() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        MCPClientAdapter("x")
    with pytest.raises(ValueError, match="exactly one"):
        MCPClientAdapter("x", command=["python"], url="https://example.com")


# ---------- auth の受け取り ----------


def test_auth_is_stored() -> None:
    auth = httpx.BasicAuth("u", "p")
    ad = MCPClientAdapter("x", url="https://example.com/mcp", auth=auth)
    assert ad.auth is auth


# ---------- elicitation の包み ----------


@pytest.mark.asyncio
async def test_wrap_elicitation_calls_user_handler() -> None:
    seen: list[tuple[str, dict]] = []

    async def handler(message: str, schema: dict) -> dict:
        seen.append((message, schema))
        return {"action": "accept", "content": {"ok": True}}

    cb = MCPClientAdapter._wrap_elicitation(handler)

    class _Params:
        message = "please confirm"
        requestedSchema: ClassVar[dict[str, str]] = {"type": "object"}

    result = await cb(None, _Params())
    assert seen == [("please confirm", {"type": "object"})]
    assert result.action == "accept"


@pytest.mark.asyncio
async def test_wrap_elicitation_accepts_elicit_result_passthrough() -> None:
    from mcp.types import ElicitResult

    async def handler(_m: str, _s: dict):
        return ElicitResult(action="decline")

    cb = MCPClientAdapter._wrap_elicitation(handler)

    class _Params:
        message = ""
        requestedSchema: ClassVar[dict[str, str]] = {}

    result = await cb(None, _Params())
    assert result.action == "decline"


# ---------- notification の包み ----------


@pytest.mark.asyncio
async def test_wrap_notification_forwards_messages() -> None:
    seen: list = []

    async def handler(msg) -> None:
        seen.append(msg)

    cb = MCPClientAdapter._wrap_notification(handler)
    await cb("hello")
    await cb({"type": "progress", "value": 0.5})
    assert seen == ["hello", {"type": "progress", "value": 0.5}]


@pytest.mark.asyncio
async def test_wrap_notification_swallows_user_exceptions() -> None:
    async def handler(_msg) -> None:
        raise RuntimeError("boom")

    cb = MCPClientAdapter._wrap_notification(handler)
    # 例外を外に出さない（デバッグログだけ残す）
    await cb("anything")
