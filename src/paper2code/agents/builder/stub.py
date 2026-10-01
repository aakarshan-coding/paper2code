"""Hand-written builder: copies a reference implementation into the workspace and runs the tests once."""
from __future__ import annotations

import shutil
from pathlib import Path

from paper2code.agents.builder.base import BuildContext


class StubBuilder:
    def __init__(self, reference_dir: Path) -> None:
        self.reference_dir = reference_dir

    def build(self, ctx: BuildContext) -> None:
        shutil.copytree(
            self.reference_dir, ctx.workspace, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        ctx.run_tests()
