"""DockerSandboxAdapter の契約テスト（subprocess.run を偽物に差し替える。実物は live テスト）。"""

import base64
import io
import re
import subprocess
import tarfile

import pytest

from omni_ai import InvalidRequestError, NotFoundError, OmniClient, ProviderConnectionError
from omni_ai.providers.docker import DEFAULT_IMAGE, DockerSandboxAdapter


def tar_b64(files: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return base64.b64encode(buf.getvalue()).decode()


class FakeRun:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        rc: int = 0,
        files=None,
        timeout=False,
        docker_rc=None,
    ):
        self.stdout, self.stderr, self.rc, self.files, self.timeout, self.docker_rc = (
            stdout,
            stderr,
            rc,
            files,
            timeout,
            docker_rc,
        )
        self.calls: list[list[str]] = []
        self.inputs: list[bytes] = []

    def __call__(self, cmd, input=None, capture_output=True, env=None, timeout=None):
        self.calls.append(list(cmd))
        if cmd[1] == "kill":
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        if cmd[1] == "run":
            self.inputs.append(input or b"")
            if self.timeout:
                raise subprocess.TimeoutExpired(cmd, timeout, output=b"partial", stderr=b"")
            if self.docker_rc is not None:
                return subprocess.CompletedProcess(cmd, self.docker_rc, b"", self.stderr)
            marker = re.search(rb"<<<OMNI-ARTIFACTS-[0-9a-f]+>>>", input).group(0)
            out = self.stdout + b"\n" + marker + b"\n" + tar_b64(self.files or {}).encode() + b"\n"
            return subprocess.CompletedProcess(cmd, self.rc, out, self.stderr)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


def sandbox(fake: FakeRun, **kw) -> DockerSandboxAdapter:
    return DockerSandboxAdapter(docker_cmd="docker", runner=fake, **kw)


def test_isolation_flags_and_result() -> None:
    fake = FakeRun(stdout=b"hello\n", files={"out.csv": b"a,b\n"})
    res = sandbox(fake).execute_python("print('hello')")
    assert (res.stdout.strip(), res.exit_code, res.timed_out) == ("hello", 0, False)
    assert res.files == {"out.csv": b"a,b\n"} and res.artifacts == [b"a,b\n"]
    assert (
        res.meta.provider == "docker"
        and res.meta.model == DEFAULT_IMAGE
        and res.meta.estimated_cost_usd == 0.0
    )
    cmd = fake.calls[0]
    for flag in ("--rm", "--read-only", "--pull"):
        assert flag in cmd
    assert cmd[cmd.index("--network") + 1] == "none" and cmd[cmd.index("--user") + 1] == "65534:65534"
    assert cmd[cmd.index("--memory") + 1] == "256m" and cmd[cmd.index("--pids-limit") + 1] == "64"
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL" and cmd[-3:] == [DEFAULT_IMAGE, "python", "-"]
    # 利用者のコードは base64 で包みに埋め込まれる（引用符などで壊れない）
    assert base64.b64encode(b"print('hello')") in fake.inputs[0]


def test_network_option_and_limits() -> None:
    fake = FakeRun()
    sandbox(fake, network=True, memory="1g", pids_limit=10).execute_python("pass")
    cmd = fake.calls[0]
    assert cmd[cmd.index("--network") + 1] == "bridge" and cmd[cmd.index("--memory") + 1] == "1g"


def test_timeout_kills_container() -> None:
    fake = FakeRun(timeout=True)
    res = sandbox(fake).execute_python("while True: pass", timeout_seconds=1)
    assert res.timed_out and res.exit_code == 137 and "timed out" in res.stderr
    kill = [c for c in fake.calls if c[1] == "kill"]
    assert kill and kill[0][2].startswith("omni-sbx-")


def test_oom_note_and_truncation() -> None:
    res = sandbox(FakeRun(rc=137)).execute_python("x")
    assert "memory limit" in res.stderr
    res = sandbox(FakeRun(stdout=b"x" * 50), max_output_bytes=10).execute_python("x")
    assert res.stdout.endswith("[sandbox] output truncated\n")


@pytest.mark.parametrize(
    ("stderr", "exc"),
    [
        (b"Unable to find image 'python:3.12-slim' locally", NotFoundError),
        (
            b"Cannot connect to the Docker daemon at unix:///x. Is the docker daemon running?",
            ProviderConnectionError,
        ),
        # Linux: docker グループに入っていない
        (
            b"permission denied while trying to connect to the Docker daemon socket at "
            b"unix:///var/run/docker.sock: dial unix /var/run/docker.sock: connect: permission denied",
            ProviderConnectionError,
        ),
        # Windows: Docker Desktop が起動していない
        (
            b"error during connect: this error may indicate that the docker daemon is not running: "
            b"open //./pipe/docker_engine: The system cannot find the file specified.",
            ProviderConnectionError,
        ),
    ],
)
def test_docker_errors(stderr: bytes, exc: type) -> None:
    with pytest.raises(exc):
        sandbox(FakeRun(stderr=stderr, docker_rc=125)).execute_python("x")


def test_empty_code_and_ensure_image() -> None:
    with pytest.raises(InvalidRequestError):
        sandbox(FakeRun()).execute_python("  ")
    fake = FakeRun()
    sandbox(fake).ensure_image()
    assert fake.calls[0][1:3] == ["image", "inspect"]


async def test_async_and_telemetry() -> None:
    ai = OmniClient(sandbox=sandbox(FakeRun(stdout=b"4\n")))
    res = await ai.sandbox.aexecute_python("print(2+2)")
    assert res.stdout.strip() == "4" and ai.telemetry.history[0].provider == "docker"
    await ai.aclose()
