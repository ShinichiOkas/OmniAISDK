"""DockerSandboxAdapter — ローカルの Docker で Python を隔離実行する（依存の追加なし。Docker が必要）。

既定の隔離（実測 2026-11-08・Docker Desktop for Mac 29.9.2 で全項目を確認）:
- ネットワーク遮断（--network none）、root 以外（uid 65534）、ルートは読み取り専用（--read-only）
- 書けるのは tmpfs の /work（作業ディレクトリ）と /tmp だけ
- メモリ 256MB（超えると終了コード 137）、CPU 1、プロセス数 64、全ケーパビリティを外す、no-new-privileges
- 時間切れは docker kill で止める（--rm でコンテナは残らない）
- 実行中にイメージを取りに行かない（--pull never）。先に ensure_image() で取得する
"""

from __future__ import annotations

import base64
import io
import logging
import subprocess
import tarfile
import time
import uuid
from collections.abc import Callable
from typing import Any, ClassVar

import anyio.to_thread

from ...capabilities import SandboxCapability
from ...compat import PlatformCompat
from ...exceptions import InvalidRequestError, NotFoundError, OmniError, ProviderConnectionError
from ...types import ResponseMeta, SandboxExecutionResult

__all__ = ["DEFAULT_IMAGE", "DockerSandboxAdapter"]

logger = logging.getLogger("omni_ai")

DEFAULT_IMAGE = "python:3.12-slim"

# コンテナ内で動かす包み。利用者のコードを実行し、終わったら /work のファイルを印付きで標準出力に書き出す
_WRAPPER = r"""
import base64, io, os, sys, tarfile, traceback
_SRC = base64.b64decode("{src}").decode("utf-8")
_code = 0
try:
    exec(compile(_SRC, "<sandbox>", "exec"), {{"__name__": "__main__"}})
except SystemExit as e:
    _code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
except BaseException:
    traceback.print_exc()
    _code = 1
finally:
    sys.stdout.flush(); sys.stderr.flush()
    buf = io.BytesIO(); total = 0; skipped = []
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for root, _dirs, names in os.walk("."):
            for n in names:
                p = os.path.join(root, n)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                if total + size > {max_artifacts}:
                    skipped.append(p); continue
                total += size; t.add(p)
    if skipped:
        sys.stderr.write("[sandbox] artifacts skipped (size limit): %s\n" % ", ".join(skipped))
    sys.__stdout__.write("\n{marker}\n" + base64.b64encode(buf.getvalue()).decode() + "\n")
    sys.__stdout__.flush()
os._exit(_code)
"""

Runner = Callable[..., Any]


class DockerSandboxAdapter(SandboxCapability):
    provider_name: ClassVar[str] = "docker"

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        memory: str = "256m",
        cpus: float = 1.0,
        pids_limit: int = 64,
        network: bool = False,
        tmpfs_size: str = "64m",
        max_output_bytes: int = 1_000_000,
        max_artifact_bytes: int = 10_000_000,
        docker_cmd: str | None = None,
        runner: Runner | None = None,
    ) -> None:
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.network = network
        self.tmpfs_size = tmpfs_size
        self.max_output_bytes = max_output_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self._docker_cmd = docker_cmd
        self._env: dict[str, str] | None = None
        self._run = runner or subprocess.run
        self._closed = False

    # ------------------------------------------------------------------ docker CLI

    def _docker(self) -> tuple[str, dict[str, str] | None]:
        if self._docker_cmd:
            return self._docker_cmd, self._env
        try:
            self._docker_cmd, self._env = PlatformCompat.resolve_docker()
        except FileNotFoundError as exc:
            raise ProviderConnectionError(str(exc), provider=self.provider_name) from exc
        return self._docker_cmd, self._env

    def _flags(self, name: str) -> list[str]:
        return [
            "run", "--rm", "-i", "--name", name, "--pull", "never",
            "--network", "bridge" if self.network else "none",
            "--memory", self.memory, "--memory-swap", self.memory, "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids_limit), "--read-only",
            "--tmpfs", f"/tmp:rw,size={self.tmpfs_size}",
            "--tmpfs", f"/work:rw,size={self.tmpfs_size},mode=1777",
            "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-w", "/work",
        ]  # fmt: skip

    def _docker_error(self, stderr: str, code: int) -> OmniError:
        low = stderr.lower()
        if "unable to find image" in low or "no such image" in low:
            return NotFoundError(
                f"image {self.image!r} is not present; call ensure_image() first", provider=self.provider_name
            )
        if "permission denied" in low and ("docker.sock" in low or "docker daemon socket" in low):
            # Linux: ソケットを使う権限が無い（docker グループに入っていない）
            return ProviderConnectionError(
                "permission denied on the Docker socket; add your user to the 'docker' group "
                f"(sudo usermod -aG docker $USER) and log in again: {stderr.strip()[:200]}",
                provider=self.provider_name,
            )
        if (
            "cannot connect to the docker daemon" in low
            or "500 internal server error" in low
            or ("error during connect" in low and "pipe" in low)  # Windows: 名前付きパイプに届かない
        ):
            return ProviderConnectionError(
                f"Docker daemon is not available (is Docker running?): {stderr.strip()[:200]}",
                provider=self.provider_name,
            )
        return OmniError(f"docker failed (exit {code}): {stderr.strip()[:300]}", provider=self.provider_name)

    def ensure_image(self, timeout: float = 600) -> None:
        """イメージが無ければ取得する（実行時には取りに行かないため）。"""
        docker, env = self._docker()
        p = self._run([docker, "image", "inspect", self.image], capture_output=True, env=env, timeout=60)
        if p.returncode == 0:
            return
        p = self._run([docker, "pull", "-q", self.image], capture_output=True, env=env, timeout=timeout)
        if p.returncode != 0:
            raise self._docker_error(p.stderr.decode(errors="replace"), p.returncode)

    # ------------------------------------------------------------------ 実行

    def _truncate(self, data: bytes) -> str:
        text = data.decode("utf-8", errors="replace")
        if len(data) > self.max_output_bytes:
            return (
                data[: self.max_output_bytes].decode("utf-8", errors="replace")
                + "\n[sandbox] output truncated\n"
            )
        return text

    def execute_python(
        self, code: str, *, timeout_seconds: int = 30, **provider_options: Any
    ) -> SandboxExecutionResult:
        if self._closed:
            raise OmniError("adapter is closed", provider=self.provider_name)
        if not code.strip():
            raise InvalidRequestError("code is empty", provider=self.provider_name)
        docker, env = self._docker()
        name = f"omni-sbx-{uuid.uuid4().hex[:12]}"
        marker = f"<<<OMNI-ARTIFACTS-{uuid.uuid4().hex}>>>"
        wrapper = _WRAPPER.format(
            src=base64.b64encode(code.encode("utf-8")).decode(),
            marker=marker,
            max_artifacts=self.max_artifact_bytes,
        )
        cmd = [docker, *self._flags(name), self.image, "python", "-"]
        started = time.perf_counter()
        timed_out = False
        try:
            p = self._run(
                cmd, input=wrapper.encode("utf-8"), capture_output=True, env=env, timeout=timeout_seconds
            )
            out, err, rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            self._run([docker, "kill", name], capture_output=True, env=env, timeout=30)
            out, err, rc = exc.stdout or b"", exc.stderr or b"", 137
        if rc == 125 and not timed_out:  # docker 自体のエラー（コンテナを起動できなかった）
            raise self._docker_error(err.decode(errors="replace"), rc)
        files: dict[str, bytes] = {}
        text = out.decode("utf-8", errors="replace")
        head, sep, tail = text.rpartition(f"\n{marker}\n")
        if sep:
            text = head
            try:
                with tarfile.open(fileobj=io.BytesIO(base64.b64decode(tail.strip())), mode="r:gz") as t:
                    for m in t.getmembers():
                        if m.isfile() and (f := t.extractfile(m)) is not None:
                            files[m.name.removeprefix("./")] = f.read()
            except (ValueError, tarfile.TarError) as exc:
                logger.warning("docker sandbox: could not decode artifacts: %s", exc)
        stdout = self._truncate(text.encode("utf-8"))
        stderr = self._truncate(err)
        if timed_out:
            stderr += f"\n[sandbox] timed out after {timeout_seconds}s\n"
        elif rc == 137:
            stderr += f"\n[sandbox] killed (exit 137; memory limit {self.memory} exceeded?)\n"
        return SandboxExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=rc,
            files=files,
            artifacts=list(files.values()),
            timed_out=timed_out,
            meta=ResponseMeta(
                provider=self.provider_name,
                model=self.image,
                estimated_cost_usd=0.0,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
        )

    async def aexecute_python(
        self, code: str, *, timeout_seconds: int = 30, **provider_options: Any
    ) -> SandboxExecutionResult:
        return await anyio.to_thread.run_sync(
            lambda: self.execute_python(code, timeout_seconds=timeout_seconds)
        )

    def close(self) -> None:
        self._closed = True

    async def aclose(self) -> None:
        self._closed = True
