"""メニュー方式のセットアップ（第 3 段で実装）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import IO


def run_setup(io: IO) -> int:
    io.print("setup menu is not implemented yet; use `omni-ai doctor` / `config` / `models`")
    return 0
