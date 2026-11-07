from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    def load(provider: str, name: str) -> bytes:
        return (FIXTURES / provider / name).read_bytes()

    return load
