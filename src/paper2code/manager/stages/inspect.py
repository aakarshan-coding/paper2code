"""Inspect stage: integrity check, hidden tests, mechanical verdict.

Code review against the paper and build-log review (the flag producers) land in build step 5.
"""
from __future__ import annotations

from paper2code.manager.freeze import MANIFEST_NAME, hash_tree, tree_digest, verify_manifest
from paper2code.manager.graph import RunContext
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import Flag, Verdict, decide


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"

    mismatches = verify_manifest(scope, record.scope_manifest_sha256)
    if record.scope_manifest_sha256 is None:
        mismatches.append(MANIFEST_NAME)  # never anchored by the manager: cannot be trusted
    if tree_digest(hash_tree(workspace)) != record.workspace_sha256:
        mismatches.append("workspace")  # not the tree that passed the public tests
    mismatches = sorted(set(mismatches))
    hidden = make_runner(ctx).run(workspace, scope / "tests" / "hidden")
    flags: list[Flag] = []
    outcome = decide(mismatches, hidden, flags)

    parts = [f"hidden tests: {len(hidden.passed)} passed, {len(hidden.failed)} failed"]
    if hidden.timed_out:
        parts.append("hidden test run timed out")
    if "workspace" in mismatches:
        parts.append("workspace differs from the tree that passed the public tests")
    scope_mismatches = [m for m in mismatches if m != "workspace"]
    if scope_mismatches:
        parts.append(f"scope integrity violated: {', '.join(scope_mismatches)}")
    parts.append("code review not performed in this build step")

    Verdict(
        outcome=outcome,
        integrity_mismatches=list(mismatches),
        hidden_passed=list(hidden.passed),
        hidden_failed=list(hidden.failed),
        flags=flags,
        summary="; ".join(parts),
        confidence=None,
    ).write(run_dir)
    record.outcome = outcome
