"""Inspect stage: integrity check, hidden tests, mechanical verdict.

Code review against the paper and build-log review (the flag producers) land in build step 5.
"""
from __future__ import annotations

from paper2code.manager.freeze import verify_manifest
from paper2code.manager.graph import RunContext
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import Flag, Verdict, decide


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"

    mismatches = verify_manifest(scope)
    hidden = make_runner(ctx).run(workspace, scope / "tests" / "hidden")
    flags: list[Flag] = []
    outcome = decide(mismatches, hidden, flags)

    parts = [f"hidden tests: {len(hidden.passed)} passed, {len(hidden.failed)} failed"]
    if hidden.timed_out:
        parts.append("hidden test run timed out")
    if mismatches:
        parts.append(f"scope integrity violated: {', '.join(mismatches)}")
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
