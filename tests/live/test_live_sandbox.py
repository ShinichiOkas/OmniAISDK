"""実物の Docker で隔離を確かめる。OMNI_LIVE_DOCKER=1 のときだけ実行する（python:3.12-slim を使う）。"""

import os

import pytest

from omni_ai import BuiltinTool, ChatMessage, OmniClient
from omni_ai.providers.docker import DockerSandboxAdapter
from omni_ai.providers.ollama import OllamaChatAdapter

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OMNI_LIVE_DOCKER"), reason="set OMNI_LIVE_DOCKER=1"),
]


@pytest.fixture(scope="module")
def sb() -> DockerSandboxAdapter:
    # OMNI_LIVE_DOCKER=1 のときに Docker が使えなければ、スキップではなく失敗にする（黙って通らないように）
    s = DockerSandboxAdapter()
    s.ensure_image()
    return s


def test_live_basic_and_files(sb: DockerSandboxAdapter) -> None:
    res = sb.execute_python("import os\nopen('out.txt','w').write('ok')\nprint('uid', os.getuid())")
    assert res.exit_code == 0 and "uid 65534" in res.stdout and res.files == {"out.txt": b"ok"}


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        ("import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)", "Network is unreachable"),
        ("open('/etc/x', 'w')", "Read-only file system"),
        ("raise ValueError('boom')", "ValueError: boom"),
    ],
)
def test_live_isolation(sb: DockerSandboxAdapter, code: str, needle: str) -> None:
    res = sb.execute_python(code)
    assert res.exit_code == 1 and needle in res.stderr


def test_live_limits(sb: DockerSandboxAdapter) -> None:
    oom = sb.execute_python("b = bytearray(600 * 1024 * 1024)")
    assert oom.exit_code == 137
    slow = sb.execute_python("while True: pass", timeout_seconds=2)
    assert slow.timed_out


@pytest.mark.skipif(not os.environ.get("OMNI_LIVE_OLLAMA"), reason="set OMNI_LIVE_OLLAMA=<model>")
async def test_live_ollama_code_execution_via_sandbox(sb: DockerSandboxAdapter) -> None:
    async with OmniClient(
        chat=OllamaChatAdapter(default_model=os.environ["OMNI_LIVE_OLLAMA"]), sandbox=sb
    ) as ai:
        res = await ai.tools.arun(
            [
                ChatMessage.user(
                    "Use Python code execution to compute the sum of all primes below 1000. "
                    "Reply with the number."
                )
            ],
            [BuiltinTool.CODE_EXECUTION],
            ollama={"think": False},
        )
        assert "docker" in [m.provider for m in ai.telemetry.history]
        assert "76127" in (res.response.content or ""), res.response.content
