"""The stub check (spec 8.3): every scoped test must fail against a workspace of empty stubs,
and fail *because of* the stubs.

Acceptance, per suite (public and hidden): the run did not time out, every file collected, at
least one test ran, nothing was skipped. Tests that pass on stubs are deleted and the suites are
run again to prove the deletion took. Every surviving failure must be a NotImplementedError from
a stub; any other failure means the test is broken or never touches the interface. A claim test
that passes on stubs, or has too few seeds, rejects the scope.
"""
from __future__ import annotations

import ast
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from paper2code.agents.scoper.schemas import CLAIM_TEST_FILE, InterfaceSpec
from paper2code.manager.scope_files import render_stubs
from paper2code.sandbox.runner import TestRunner, TestRunResult

TESTS_DO_NOT_COLLECT = "tests_do_not_collect"
TRIVIAL_CLAIM_TEST = "trivial_claim_test"
INSUFFICIENT_SEEDS = "insufficient_seeds"
NO_TESTS = "no_tests"
NO_PUBLIC_TESTS = "no_public_tests"
NO_HIDDEN_TESTS = "no_hidden_tests"
TESTS_SKIPPED = "tests_skipped"
WRONG_FAILURES = "tests_fail_for_other_reasons"
STUB_CHECK_TIMEOUT = "stub_check_timeout"
STUB_CHECK_FAILED = "stub_check_failed"
PRUNE_FAILED = "prune_failed"
CLAIM_STEM = CLAIM_TEST_FILE.removesuffix(".py")
_OK_RETURNCODES = (0, 1, 5)  # all passed, some failed, nothing collected


@dataclass
class StubCheckResult:
    removed: list[str] = field(default_factory=list)
    errored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    wrong_failures: list[str] = field(default_factory=list)
    claim_test_ids: list[str] = field(default_factory=list)
    public_failed: int = 0
    hidden_failed: int = 0
    reject_reason: str | None = None


def _function_name(test_id: str) -> str:
    return test_id.split("::", 1)[1].split("[", 1)[0]


def _file_stem(test_id: str) -> str:
    """'test_x.TestCls::test_a[0]' -> 'test_x'. The classname is the file stem plus any class path."""
    return test_id.split("::", 1)[0].split(".", 1)[0]


def remove_tests(file_path: Path, names: set[str]) -> list[str]:
    """Delete the named test functions (top level or inside classes, decorators included).

    A class whose every statement is removed is removed whole so the file stays valid Python.
    """
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    removed: list[str] = []

    def span_of(node) -> tuple[int, int]:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return start - 1, node.end_lineno

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            spans.append(span_of(node))
            removed.append(node.name)
        elif isinstance(node, ast.ClassDef):
            doomed = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
            if not doomed:
                continue
            removed.extend(n.name for n in doomed)
            if len(doomed) == len(node.body):
                spans.append(span_of(node))
            else:
                spans.extend(span_of(n) for n in doomed)
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    file_path.write_text("".join(lines), encoding="utf-8")
    return removed


def _suite_problem(result: TestRunResult, label: str) -> str | None:
    if result.timed_out:
        return STUB_CHECK_TIMEOUT
    if result.errored:
        return TESTS_DO_NOT_COLLECT
    if result.returncode not in _OK_RETURNCODES:
        return STUB_CHECK_FAILED
    if not result.passed and not result.failed:
        return NO_PUBLIC_TESTS if label == "public" else NO_HIDDEN_TESTS
    if result.skipped:
        return TESTS_SKIPPED
    return None


def _prune(tests_dir: Path, result: TestRunResult) -> tuple[list[str], bool]:
    """Delete every passing test. Returns (ids requested, whether every requested function was cut)."""
    by_file: dict[str, set[str]] = {}
    for test_id in result.passed:
        by_file.setdefault(_file_stem(test_id), set()).add(_function_name(test_id))
    complete = True
    for stem, names in by_file.items():
        path = tests_dir / f"{stem}.py"
        if not path.exists():
            complete = False
            continue
        cut = set(remove_tests(path, names))
        if cut != names:
            complete = False
    return list(result.passed), complete


def run_stub_check(scope_dir: Path, interface: InterfaceSpec, runner: TestRunner, min_seeds: int) -> StubCheckResult:
    public = scope_dir / "tests" / "public"
    hidden = scope_dir / "tests" / "hidden"
    out = StubCheckResult()
    with tempfile.TemporaryDirectory(prefix="p2c-stubs-") as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        (workspace / f"{interface.module}.py").write_text(render_stubs(interface), encoding="utf-8")

        def run_both() -> tuple[TestRunResult, TestRunResult]:
            return runner.run(workspace, public), runner.run(workspace, hidden)

        pub, hid = run_both()
        out.errored = list(pub.errored) + list(hid.errored)
        out.skipped = list(pub.skipped) + list(hid.skipped)
        problem = _suite_problem(pub, "public") or _suite_problem(hid, "hidden")
        if problem is not None:
            out.reject_reason = problem
            return out
        claim_passed_on_stubs = any(_file_stem(t) == CLAIM_STEM for t in pub.passed)
        if pub.passed or hid.passed:
            removed_pub, ok_pub = _prune(public, pub)
            removed_hid, ok_hid = _prune(hidden, hid)
            out.removed = removed_pub + removed_hid
            if not (ok_pub and ok_hid):
                out.reject_reason = PRUNE_FAILED
                return out
            pub, hid = run_both()  # prove the deletion took
            problem = _suite_problem(pub, "public") or _suite_problem(hid, "hidden")
            if problem is not None:
                out.reject_reason = problem
                return out
            if pub.passed or hid.passed:
                out.reject_reason = PRUNE_FAILED
                return out
    out.public_failed = len(pub.failed)
    out.hidden_failed = len(hid.failed)
    out.claim_test_ids = [t for t in pub.failed if _file_stem(t) == CLAIM_STEM]
    messages = {**pub.messages, **hid.messages}
    out.wrong_failures = [t for t in list(pub.failed) + list(hid.failed) if not messages.get(t, "").startswith("NotImplementedError")]
    if out.wrong_failures:
        out.reject_reason = WRONG_FAILURES
    elif claim_passed_on_stubs or not out.claim_test_ids:
        out.reject_reason = TRIVIAL_CLAIM_TEST
    elif len(out.claim_test_ids) < min_seeds:
        out.reject_reason = INSUFFICIENT_SEEDS
    return out
