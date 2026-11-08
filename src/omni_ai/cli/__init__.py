"""omni-ai コマンド。引数なしで起動するとメニュー（setup）、引数つきなら各サブコマンドを実行する。

omni-ai                 メニュー方式のセットアップ
omni-ai doctor          環境の診断
omni-ai config path|show|validate
omni-ai models list|catalog|size|pull|remove
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

__all__ = ["main"]

_MARK = {"ok": "✓", "warn": "!", "fail": "✗", "skip": "-"}


class IO:
    """入出力（テストで差し替える）。"""

    def __init__(
        self,
        out: TextIO | None = None,
        ask: Callable[[str], str] | None = None,
        secret: Callable[[str], str] | None = None,
    ) -> None:
        self.out = out or sys.stdout
        self._ask = ask or input
        # 秘密の入力は画面に出さない（テストでは ask と同じものを使う）
        if secret is not None:
            self._secret = secret
        elif ask is not None:
            self._secret = ask
        else:
            import getpass

            self._secret = getpass.getpass

    def print(self, *parts: object) -> None:
        print(*parts, file=self.out)

    def ask(self, prompt: str) -> str:
        return self._ask(prompt)

    def secret(self, prompt: str) -> str:
        return self._secret(prompt)

    def confirm(self, prompt: str, *, yes: bool = False) -> bool:
        if yes:
            return True
        return self.ask(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def cmd_doctor(io: IO) -> int:
    from .checks import run_checks

    checks = run_checks()
    width = max(len(c.name) for c in checks)
    for c in checks:
        io.print(f"{_MARK[c.status]} {c.name.ljust(width)}  {c.detail}")
        if c.hint and c.status in ("fail", "warn", "skip"):
            io.print(f"  {' ' * width}  → {c.hint}")
    failed = [c for c in checks if c.status == "fail"]
    io.print(f"\n{len(failed)} problem(s)" if failed else "\nall required checks passed")
    return 1 if failed else 0


def cmd_config(io: IO, action: str) -> int:
    from ..settings import config_path, load_config, secrets_path, validate_files

    if action == "path":
        io.print(f"config : {config_path()}")
        io.print(f"secrets: {secrets_path()}")
        return 0
    if action == "show":
        from ..settings import dump_toml

        cfg = config_path()
        io.print(f"# {cfg}" + ("" if cfg.exists() else " (not created yet)"))
        io.print(dump_toml(load_config()) if cfg.exists() else "")
        io.print(f"# secrets: {secrets_path()} (values are not shown)")
        return 0
    _, issues = validate_files()
    for i in issues:
        io.print(str(i))
    errors = [i for i in issues if i.level == "error"]
    io.print("valid" if not issues else f"{len(errors)} error(s), {len(issues) - len(errors)} warning(s)")
    return 1 if errors else 0


def _fmt_size(n: int | None) -> str:
    return "unknown size" if n is None else (f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB")


def cmd_models(io: IO, action: str, name: str | None, yes: bool) -> int:
    from . import models

    if action == "catalog":
        for key, (_, target) in models.CATALOG.items():
            io.print(f"{key:22s} {target}")
        io.print("ollama:<model>         e.g. ollama:tev1:0.8b, ollama:nomic-embed-text")
        io.print("docker:<image>         e.g. docker:python:3.12-slim")
        return 0
    if action == "list":
        from ..providers.fastembed._lazy import default_cache_dir

        io.print(f"[local cache] {default_cache_dir()}")
        cached = models.list_hf_cache()
        for m in cached:
            io.print(f"  {m.repo:60s} {_fmt_size(m.size_bytes)}")
        if not cached:
            io.print("  (empty)")
        io.print("[ollama]")
        for om in models.ollama_models():
            io.print(f"  {om.get('name', ''):60s} {_fmt_size(om.get('size'))}")
        return 0
    if not name:
        io.print("a model name is required (see `omni-ai models catalog`)")
        return 2
    if action == "size":
        io.print(f"{name}: {_fmt_size(models.remote_size(name))}")
        return 0
    if action == "pull":
        size = models.remote_size(name)
        if not io.confirm(f"Download {name} ({_fmt_size(size)})?", yes=yes):
            io.print("cancelled")
            return 1
        io.print(models.pull(name))
        return 0
    if not io.confirm(f"Remove {name}?", yes=yes):
        io.print("cancelled")
        return 1
    io.print(models.remove(name))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omni-ai", description="OmniAI SDK: setup, diagnosis and model management"
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser("setup", help="menu-driven setup (default)")
    sub.add_parser("doctor", help="check the environment and the configuration")
    c = sub.add_parser("config", help="show / validate the configuration")
    c.add_argument("action", choices=["path", "show", "validate"])
    m = sub.add_parser("models", help="list / download / remove models")
    m.add_argument("action", choices=["list", "catalog", "size", "pull", "remove"])
    m.add_argument("name", nargs="?")
    m.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    return p


def main(argv: Sequence[str] | None = None, io: IO | None = None) -> int:
    args = build_parser().parse_args(argv)
    io = io or IO()
    if args.command == "doctor":
        return cmd_doctor(io)
    if args.command == "config":
        return cmd_config(io, args.action)
    if args.command == "models":
        return cmd_models(io, args.action, args.name, args.yes)
    from .setup import run_setup

    return run_setup(io)
