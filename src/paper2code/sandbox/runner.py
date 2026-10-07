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

from paper2code.manager.freeze import hash_tree, tree_digest

# Must agree with manager/freeze.py _IGNORED_DIRS; see the note there.
_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")

# The workspace is untrusted. pytest must be imported before the workspace is on sys.path, or a
# workspace `pytest.py` (or `sitecustomize.py` at interpreter start-up) replaces the test runner.
# `python -I` keeps the cwd and the script directory off sys.path and ignores PYTHON* env vars.
_BOOTSTRAP = """\
import sys
import pytest  # resolved from site-packages: the workspace is not on sys.path yet
sys.path.insert(0, sys.argv[1])
sys.exit(pytest.main(sys.argv[2:]))
"""
_STRIPPED_ENV = ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS")


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
    workspace_sha256: str = ""  # tree_digest of the snapshot that was tested
    errored: tuple[str, ...] = ()  # collection/setup errors; a subset of `failed`

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


def parse_junit_errors(path: Path) -> tuple[str, ...]:
    """Test ids whose JUnit entry is an <error> (collection or setup failure), not a <failure>."""
    root = ET.parse(path).getroot()
    return tuple(
        f"{tc.get('classname', '')}::{tc.get('name', '')}"
        for tc in root.iter("testcase")
        if any(child.tag == "error" for child in tc)
    )


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
            bootstrap = Path(tmp) / "bootstrap.py"
            bootstrap.write_text(_BOOTSTRAP, encoding="utf-8")
            digest = tree_digest(hash_tree(snapshot))
            env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            cmd = [
                sys.executable, "-I", "-B", str(bootstrap), str(snapshot), str(tests_copy),
                "-q", "-p", "no:cacheprovider",
                f"--junitxml={report}", "--rootdir", str(tests_copy),
            ]
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd, cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                output = (exc.stdout or "") + (exc.stderr or "")
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                return TestRunResult((), (), -1, True, time.monotonic() - start, 0.0, output, digest)
            duration = time.monotonic() - start
            passed, failed = parse_junit(report) if report.exists() else ((), ())
            errored = parse_junit_errors(report) if report.exists() else ()
            return TestRunResult(
                passed, failed, proc.returncode, False, duration, 0.0, proc.stdout + proc.stderr, digest, errored,
            )
