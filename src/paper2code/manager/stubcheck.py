"""The stub check (spec 8.3): every scoped test must fail against a workspace of empty stubs.

A test that passes on stubs proves nothing and is deleted; a claim test that passes on stubs, or
that has too few seeds, rejects the scope; a test file that cannot even be imported against the
stubs would fail for the wrong reason forever, so it rejects the scope too.
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
CLAIM_STEM = CLAIM_TEST_FILE.removesuffix(".py")


@dataclass
class StubCheckResult:
    removed: list[str] = field(default_factory=list)
    errored: list[str] = field(default_factory=list)
    claim_test_ids: list[str] = field(default_factory=list)
    public_failed: int = 0
    hidden_failed: int = 0
    reject_reason: str | None = None


def _function_name(test_id: str) -> str:
    return test_id.split("::", 1)[1].split("[", 1)[0]


def _file_stem(test_id: str) -> str:
    return test_id.split("::", 1)[0]


def remove_tests(file_path: Path, names: set[str]) -> list[str]:
    """Delete the named top-level functions (with their decorators) from a test file."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    spans: list[tuple[int, int]] = []
    removed: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans.append((start - 1, node.end_lineno))
            removed.append(node.name)
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    file_path.write_text("".join(lines), encoding="utf-8")
    return removed


def _prune_passing(tests_dir: Path, result: TestRunResult) -> list[str]:
    by_file: dict[str, set[str]] = {}
    for test_id in result.passed:
        by_file.setdefault(_file_stem(test_id), set()).add(_function_name(test_id))
    for stem, names in by_file.items():
        remove_tests(tests_dir / f"{stem}.py", names)
    return list(result.passed)


def run_stub_check(scope_dir: Path, interface: InterfaceSpec, runner: TestRunner, min_seeds: int) -> StubCheckResult:
    public = scope_dir / "tests" / "public"
    hidden = scope_dir / "tests" / "hidden"
    out = StubCheckResult()
    with tempfile.TemporaryDirectory(prefix="p2c-stubs-") as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        (workspace / f"{interface.module}.py").write_text(render_stubs(interface), encoding="utf-8")
        pub = runner.run(workspace, public)
        hid = runner.run(workspace, hidden)
    out.errored = list(pub.errored) + list(hid.errored)
    if out.errored:
        out.reject_reason = TESTS_DO_NOT_COLLECT
        return out
    out.removed = _prune_passing(public, pub) + _prune_passing(hidden, hid)
    out.public_failed = len(pub.failed)
    out.hidden_failed = len(hid.failed)
    out.claim_test_ids = [t for t in pub.failed if _file_stem(t) == CLAIM_STEM]
    if not pub.failed and not hid.failed:
        out.reject_reason = NO_TESTS
    elif any(_file_stem(t) == CLAIM_STEM for t in pub.passed) or not out.claim_test_ids:
        out.reject_reason = TRIVIAL_CLAIM_TEST
    elif len(out.claim_test_ids) < min_seeds:
        out.reject_reason = INSUFFICIENT_SEEDS
    return out
