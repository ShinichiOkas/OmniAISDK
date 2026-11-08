"""OS差分吸収レイヤー（仕様書 v3.1 §9.3、ADR-01）。

OS の判定（``sys.platform`` / ``os.name``）はこのモジュールの中でだけ行う。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unicodedata
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_cache_dir, user_config_dir

__all__ = ["APP_NAME", "DEFAULT_OLLAMA_PORT", "PlatformCompat"]

APP_NAME = "omni_ai"
DEFAULT_OLLAMA_PORT = 11434
_LISTEN_ALL = {"0.0.0.0", "", "::", "[::]"}


class PlatformCompat:
    """Windows / Linux / macOS の OS 差分を一元吸収するファサード。"""

    @staticmethod
    def is_windows() -> bool:
        return sys.platform == "win32"

    @staticmethod
    def is_macos() -> bool:
        return sys.platform == "darwin"

    @staticmethod
    def is_linux() -> bool:
        return sys.platform.startswith("linux")

    @staticmethod
    def get_config_dir(create: bool = True) -> Path:
        path = Path(user_config_dir(APP_NAME, appauthor=False))
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_cache_dir(create: bool = True) -> Path:
        path = Path(user_cache_dir(APP_NAME, appauthor=False))
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def make_private(path: Path) -> None:
        """ファイルを本人だけが読み書きできるようにする（POSIX は 0600）。

        Windows はユーザープロファイル配下の既定の ACL に任せる（chmod では制御できない）。
        """
        if not PlatformCompat.is_windows():
            path.chmod(0o600)

    @staticmethod
    def normalize_path_str(path: Path) -> str:
        """macOS の NFD 濁点を NFC に正規化し、POSIX 形式の文字列にする。"""
        return unicodedata.normalize("NFC", path.resolve().as_posix())

    @staticmethod
    def normalize_name(name: str) -> str:
        return unicodedata.normalize("NFC", name)

    @staticmethod
    @contextmanager
    def safe_temp_file(suffix: str = ".tmp", data: bytes | None = None) -> Generator[Path, None, None]:
        """Windows の排他ロックを避けるため、一度閉じてから Path を渡す一時ファイル。"""
        fd, raw_path = tempfile.mkstemp(suffix=suffix)
        temp_path = Path(raw_path)
        try:
            with os.fdopen(fd, "wb") as f:
                if data is not None:
                    f.write(data)
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def resolve_executable(cmd: str) -> str:
        """Windows の .cmd/.exe 解決と macOS GUI 起動時の PATH 補完を含めて実行ファイルを解決する。"""
        resolved = shutil.which(cmd)
        if resolved:
            return resolved
        if PlatformCompat.is_macos():
            extra = ["/opt/homebrew/bin", "/usr/local/bin", str(Path("~/.local/bin").expanduser())]
            resolved = shutil.which(cmd, path=os.pathsep.join(extra))
            if resolved:
                return resolved
        raise FileNotFoundError(f"Executable {cmd!r} not found in PATH on {sys.platform}.")

    @staticmethod
    def resolve_docker() -> tuple[str, dict[str, str]]:
        """docker CLI のパスと、実行時の環境変数を返す。

        Docker Desktop は CLI を PATH に入れないことがある（実測: macOS で /usr/local/bin に無かった）。
        資格情報ヘルパー（docker-credential-desktop）も同じ bin にあるため、その bin を PATH の先頭に足す。
        """
        env = dict(os.environ)
        found = shutil.which("docker")
        if found:
            return found, env
        candidates: list[Path] = []
        if PlatformCompat.is_macos():
            candidates = [
                Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
                Path("~/.docker/bin/docker").expanduser(),
                Path("/usr/local/bin/docker"),
                Path("/opt/homebrew/bin/docker"),
            ]
        elif PlatformCompat.is_windows():
            pf = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            candidates = [pf / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"]
        else:
            candidates = [Path("/usr/bin/docker"), Path("/usr/local/bin/docker")]
        for c in candidates:
            if c.exists():
                env["PATH"] = str(c.parent) + os.pathsep + env.get("PATH", "")
                return str(c), env
        raise FileNotFoundError("docker CLI not found (is Docker Desktop / Docker Engine installed?)")

    @staticmethod
    def is_in_container() -> bool:
        if Path("/.dockerenv").exists():
            return True
        if PlatformCompat.is_linux():
            try:
                cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False
            return any(token in cgroup for token in ("docker", "containerd", "kubepods", "podman"))
        return False

    @staticmethod
    def normalize_ollama_host(value: str) -> str:
        """``OLLAMA_HOST`` を Ollama 本体と同じ規則で URL に正規化する。

        - スキーム省略 → http、ポート省略 → 11434
        - スキーム明示でポート省略 → http は 80、https は 443
        - 待ち受け用アドレス (0.0.0.0 / ::) は接続先として 127.0.0.1 に読み替える
        """
        raw = value.strip()
        has_scheme = "://" in raw
        parts = urlsplit(raw if has_scheme else f"http://{raw}")
        scheme = parts.scheme or "http"
        host = parts.hostname or ""
        if host in _LISTEN_ALL or f"[{host}]" in _LISTEN_ALL:
            host = "127.0.0.1"
        try:
            port = parts.port
        except ValueError:
            port = None
        if port is None:
            port = (443 if scheme == "https" else 80) if has_scheme else DEFAULT_OLLAMA_PORT
        host_part = f"[{host}]" if ":" in host else host
        path = parts.path.rstrip("/")
        return f"{scheme}://{host_part}:{port}{path}"

    @staticmethod
    def resolve_ollama_base_url(configured_url: str | None = None) -> str:
        """引数 → OLLAMA_HOST → コンテナ内なら host.docker.internal → 127.0.0.1 の順で解決する。"""
        if configured_url:
            return configured_url.rstrip("/")
        if env_url := os.environ.get("OLLAMA_HOST"):
            return PlatformCompat.normalize_ollama_host(env_url)
        return f"http://127.0.0.1:{DEFAULT_OLLAMA_PORT}"

    @staticmethod
    def container_host_fallback_url() -> str | None:
        """コンテナ内で 127.0.0.1 に接続できなかったときに 1 回だけ試す URL。"""
        if PlatformCompat.is_in_container():
            return f"http://host.docker.internal:{DEFAULT_OLLAMA_PORT}"
        return None
