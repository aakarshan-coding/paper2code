"""Local-mode helpers that stand in for stages not built yet."""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from paper2code.config import Config
from paper2code.manager.freeze import manifest_sha256, write_manifest
from paper2code.manager.record import Caps, Paper, RunRecord, create_run


def init_run(runs_root: Path, scope_src: Path, paper: Paper, today: date, config: Config) -> RunRecord:
    """Create a run already at the scope stage from a pre-written scope directory, frozen."""
    caps = Caps(
        test_runs=config.caps.test_runs,
        wall_clock_s=config.caps.wall_clock_s,
        stall_n=config.caps.stall_n,
    )
    record = create_run(runs_root, today, caps, config.budget.limit_usd)
    shutil.copytree(
        scope_src, record.run_dir / "scope", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    write_manifest(record.run_dir / "scope")
    record.scope_manifest_sha256 = manifest_sha256(record.run_dir / "scope")
    record.paper = paper
    record.stage = "scope"
    record.save()
    return record
