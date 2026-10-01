"""Builder interface. The manager owns run_tests and give_up; the builder only calls them."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from paper2code.sandbox.runner import TestRunResult


class BuildFinished(Exception):
    """Raised by run_tests or give_up once the manager has ended the session."""


@dataclass
class BuildContext:
    workspace: Path
    spec_path: Path
    interface_path: Path
    public_tests: Path
    run_tests: Callable[[], TestRunResult]
    give_up: Callable[[str], None]


class Builder(Protocol):
    def build(self, ctx: BuildContext) -> None: ...
