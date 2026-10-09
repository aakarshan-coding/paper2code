import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
_SECRETS = ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")


@pytest.fixture
def canary_dir() -> Path:
    return FIXTURES / "canary"


@pytest.fixture(autouse=True)
def no_real_credentials(monkeypatch):
    """The unit suite never calls a model or pushes anywhere. Hiding the operator's keys makes an
    accidental real call fail on every machine, not only on CI. Opt-in live tests keep them."""
    if any(k.startswith("PAPER2CODE_LIVE") for k in os.environ):
        return
    for name in _SECRETS:
        monkeypatch.delenv(name, raising=False)
