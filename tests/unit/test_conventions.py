"""師匠の確定ルールを機械的に守るためのテスト。"""

from pathlib import Path

SRC = Path(__file__).parent.parent.parent / "src" / "omni_ai"


def test_no_mnemo_name_in_code() -> None:
    """「Mnemo」という名称はコードに出さない（confirmed 2026-11-08: provider-naming-engine-name-no-mnemo）。"""
    hits = [
        f"{p.relative_to(SRC)}:{i}"
        for p in SRC.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "mnemo" in line.lower()
    ]
    assert hits == [], f"'Mnemo' must not appear in code: {hits}"
