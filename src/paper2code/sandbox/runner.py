"""Test runners. The local one runs pytest in a subprocess over a snapshot of the workspace.

Build step 4 adds a Modal-backed runner with the same interface.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


@dataclass(frozen=True)
class TestRunResult:
    __test__ = False  # not a pytest test class despite the name

    passed: tuple[str, ...]
    failed: tuple[str, ...]  # failures, errors, and skips: anything that did not pass
    returncode: int
    timed_out: bool
    duration_s: float
    gpu_seconds: float
    output: str

    @property
    def all_passed(self) -> bool:
        """Strict: exit 0, no timeout, at least one test ran, nothing failed."""
        return (not self.timed_out) and self.returncode == 0 and bool(self.passed) and not self.failed

    @property
    def failing_set(self) -> frozenset[str]:
        return frozenset(self.failed)


class TestRunner(Protocol):
    __test__ = False

    def run(self, workspace: Path, tests_dir: Path) -> TestRunResult: ...


def parse_junit(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split JUnit XML test cases into (passed, not-passed) ids of the form classname::name."""
    root = ET.parse(path).getroot()
    passed: list[str] = []
    failed: list[str] = []
    for tc in root.iter("testcase"):
        test_id = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        child_tags = {child.tag for child in tc}
        if child_tags & {"failure", "error", "skipped"}:
            failed.append(test_id)
        else:
            passed.append(test_id)
    return tuple(passed), tuple(failed)


class LocalTestRunner:
    """Runs `tests_dir` against a snapshot copy of `workspace` with a hard timeout. No GPU."""

    def __init__(self, timeout_s: int) -> None:
        self.timeout_s = timeout_s

    def run(self, workspace: Path, tests_dir: Path) -> TestRunResult:
        with tempfile.TemporaryDirectory(prefix="p2c-run-") as tmp:
            snapshot = Path(tmp) / "snapshot"
            tests_copy = Path(tmp) / "tests"
            shutil.copytree(workspace, snapshot, ignore=_IGNORE)
            shutil.copytree(tests_dir, tests_copy, ignore=_IGNORE)
            report = Path(tmp) / "report.xml"
            env = {
                **os.environ,
                "PYTHONPATH": str(snapshot),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            cmd = [
                sys.executable, "-m", "pytest", str(tests_copy),
                "-q", "-p", "no:cacheprovider",
                f"--junitxml={report}", "--rootdir", str(tests_copy),
            ]
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd, cwd=snapshot, env=env, capture_output=True, text=True, timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                output = (exc.stdout or "") + (exc.stderr or "")
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                return TestRunResult((), (), -1, True, time.monotonic() - start, 0.0, output)
            duration = time.monotonic() - start
            passed, failed = parse_junit(report) if report.exists() else ((), ())
            return TestRunResult(passed, failed, proc.returncode, False, duration, 0.0, proc.stdout + proc.stderr)
