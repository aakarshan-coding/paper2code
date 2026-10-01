from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def canary_dir() -> Path:
    return FIXTURES / "canary"
