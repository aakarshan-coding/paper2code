"""Test runners. The local one runs pytest in a subprocess over a snapshot of the workspace.

Build step 4 adds a Modal-backed runner with the same interface.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from paper2code.manager.freeze import hash_tree, tree_digest

# Must agree with manager/freeze.py _IGNORED_DIRS; see the note there.
_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")

# The workspace is untrusted. pytest must be imported before the workspace is on sys.path, or a
# workspace `pytest.py` (or `sitecustomize.py` at interpreter start-up) replaces the test runner.
# `python -I` keeps the cwd and the script directory off sys.path and ignores PYTHON* env vars.
_BOOTSTRAP = """\
import json
import sys
import pytest  # resolved from site-packages: the workspace is not on sys.path yet



def _witness(outcomes_path):
    # Records what each test actually did, before any report object exists. A workspace that
    # patches pytest's reporting cannot change what this hook saw. The list and the path live in
    # this closure, not on the module, so workspace code reaching __main__ finds nothing to swap.
    seen = []

    class _Witness:
        @pytest.hookimpl(tryfirst=True)
        def pytest_runtest_makereport(self, item, call):
            seen.append({"nodeid": item.nodeid, "when": call.when, "raised": call.excinfo is not None,
                         "xfail": item.get_closest_marker("xfail") is not None})

        def pytest_sessionfinish(self, session, exitstatus):
            with open(outcomes_path, "w", encoding="utf-8") as fh:
                json.dump(seen, fh)

    return _Witness()


_plugin = _witness(sys.argv[2])
sys.path.insert(0, sys.argv[1])
sys.exit(pytest.main(sys.argv[3:], plugins=[_plugin]))
"""
_STRIPPED_ENV = ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS")
# Model-written test code runs in this subprocess. It must not see the operator's credentials.
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$", re.IGNORECASE)
_SECRET_PREFIXES = ("OPENAI_", "ANTHROPIC_", "CLAUDE_", "MODAL_", "AWS_", "GITHUB_", "GH_", "HF_", "AZURE_", "GOOGLE_")


def _is_secret(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name)) or name.upper().startswith(_SECRET_PREFIXES)


def scrubbed_environment() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV and not _is_secret(k)}


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
    skipped: tuple[str, ...] = ()  # skipped or xfailed; a subset of `failed`
    messages: dict[str, str] = field(default_factory=dict)  # test id -> failure/error message
    altered: tuple[str, ...] = ()  # reported passed by JUnit but seen raising by the runner's own witness hook

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


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it spawned. On Windows a plain kill leaves grandchildren
    holding the stdout pipe, and communicate() then waits for them."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL, capture_output=True,  # no inherited stdin: it may be closed under pytest
        )
    else:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _decode(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")


def run_killable(args, *, cwd, env: dict, timeout_s: float, shell: bool = False) -> tuple[int, str, str, bool]:
    """Run a command in its own process group with a hard deadline that kills the whole tree.

    Returns (returncode, stdout, stderr, timed_out). Output is decoded as UTF-8 with replacement so
    a stray byte in a training log never raises.
    """
    kwargs: dict = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=shell)
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return proc.returncode, _decode(out), _decode(err), False
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=15)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            out, err = b"", b""
        return -1, _decode(out), _decode(err), True


def parse_junit_details(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """(skipped ids, {id: message}) where message is the failure/error text pytest recorded."""
    root = ET.parse(path).getroot()
    skipped: list[str] = []
    messages: dict[str, str] = {}
    for tc in root.iter("testcase"):
        test_id = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        for child in tc:
            if child.tag == "skipped":
                skipped.append(test_id)
            elif child.tag in ("failure", "error"):
                messages[test_id] = (child.get("message") or (child.text or "").strip())[:500]
    return tuple(skipped), messages


def _junit_id(nodeid: str) -> str:
    """pytest's JUnit id for a nodeid: 'dir/test_x.py::TestC::test_a[1]' -> 'dir.test_x.TestC::test_a[1]'."""
    path, _, rest = nodeid.partition("::")
    module = path[:-3] if path.endswith(".py") else path
    parts = [module.replace("/", ".")] + rest.split("::")
    return ".".join(parts[:-1]) + "::" + parts[-1]


def cross_check(outcomes: Path, passed, failed, errored, skipped, messages):
    """Move any test the witness hook saw raising (call phase, not xfail) out of `passed`.

    Returns (passed, failed, errored, skipped, messages, altered). A missing or unreadable outcomes
    file (pytest never finished) changes nothing."""
    unchanged = (tuple(passed), tuple(failed), tuple(errored), tuple(skipped), dict(messages), ())
    if not outcomes.exists():
        return unchanged
    try:
        seen = json.loads(outcomes.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unchanged
    raised = {_junit_id(s["nodeid"]) for s in seen if s.get("when") == "call" and s.get("raised") and not s.get("xfail")}
    passed_l, failed_l, messages_d, altered = list(passed), list(failed), dict(messages), []
    for test_id in list(passed_l):
        if test_id in raised:
            passed_l.remove(test_id)
            failed_l.append(test_id)
            messages_d[test_id] = "outcome altered in-process: the test raised but was reported as passed"
            altered.append(test_id)
    return tuple(passed_l), tuple(failed_l), tuple(errored), tuple(skipped), messages_d, tuple(altered)


class LocalTestRunner:
    """Runs `tests_dir` against a snapshot copy of `workspace` with a hard timeout. No GPU."""

    def __init__(self, timeout_s: int) -> None:
        self.timeout_s = timeout_s

    def run(self, workspace: Path, tests_dir: Path) -> TestRunResult:
        with tempfile.TemporaryDirectory(prefix="p2c-run-", ignore_cleanup_errors=True) as tmp:
            snapshot = Path(tmp) / "snapshot"
            tests_copy = Path(tmp) / "tests"
            shutil.copytree(workspace, snapshot, ignore=_IGNORE)
            shutil.copytree(tests_dir, tests_copy, ignore=_IGNORE)
            report = Path(tmp) / "report.xml"
            outcomes = Path(tmp) / "outcomes.json"
            bootstrap = Path(tmp) / "bootstrap.py"
            bootstrap.write_text(_BOOTSTRAP, encoding="utf-8")
            digest = tree_digest(hash_tree(snapshot))
            env = scrubbed_environment()
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            cmd = [
                sys.executable, "-I", "-B", str(bootstrap), str(snapshot), str(outcomes), str(tests_copy),
                "-q", "-p", "no:cacheprovider",
                f"--junitxml={report}", "--rootdir", str(tests_copy),
            ]
            start = time.monotonic()
            returncode, stdout, stderr, timed_out = run_killable(cmd, cwd=tmp, env=env, timeout_s=self.timeout_s)
            duration = time.monotonic() - start
            if timed_out:
                return TestRunResult((), (), -1, True, duration, 0.0, stdout + stderr, digest)
            passed, failed = parse_junit(report) if report.exists() else ((), ())
            errored = parse_junit_errors(report) if report.exists() else ()
            skipped, messages = parse_junit_details(report) if report.exists() else ((), {})
            passed, failed, errored, skipped, messages, altered = cross_check(outcomes, passed, failed, errored, skipped, messages)
            return TestRunResult(
                passed, failed, returncode, False, duration, 0.0, stdout + stderr, digest, errored, skipped, messages, altered,
            )
