"""テスト用の MCP サーバー（stdio）。python demo_server.py で起動する。"""

import base64
import time

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.types import Image

server = MCPServer("omni-demo")

# 1x1 の透明 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
def weather(city: str) -> str:
    """Get current weather for a city."""
    return {"tokyo": "sunny, 20C", "osaka": "rainy, 15C"}.get(city.lower(), "unknown")


@server.tool()
def fail(reason: str) -> str:
    """Always fails."""
    raise ValueError(f"failed on purpose: {reason}")


@server.tool()
def pixel() -> Image:
    """Return a 1x1 PNG image."""
    return Image(data=_PNG, format="png")


@server.tool()
def slow(seconds: float) -> str:
    """Sleep and return."""
    time.sleep(seconds)
    return "done"


if __name__ == "__main__":
    server.run()
