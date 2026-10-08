"""Inspect stage: integrity check, hidden tests, mechanical reviews, the inspector's code review,
and the mechanical verdict. Writes verdict.json only."""
from __future__ import annotations

from paper2code.agents.inspector.bundle import build_bundle
from paper2code.agents.inspector.inspector import review_workspace_with_model, to_flags
from paper2code.llm.base import LLMBadOutput, Usage
from paper2code.llm.factory import make_chat_model
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.freeze import MANIFEST_NAME, hash_tree, tree_digest, verify_manifest
from paper2code.manager.graph import RunContext
from paper2code.manager.record import RunRecord
from paper2code.manager.review import review_build_log, review_workspace
from paper2code.manager.verdict import Flag, Verdict, decide


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"

    # 1. Integrity (spec 10.3.1): the frozen scope and the tree that passed the public tests.
    mismatches = verify_manifest(scope, record.scope_manifest_sha256)
    if record.scope_manifest_sha256 is None:
        mismatches.append(MANIFEST_NAME)  # never anchored by the manager: cannot be trusted
    if tree_digest(hash_tree(workspace)) != record.workspace_sha256:
        mismatches.append("workspace")  # not the tree that passed the public tests
    mismatches = sorted(set(mismatches))

    # 2. Hidden tests, once (spec 10.3.2).
    hidden = make_runner(ctx).run(workspace, scope / "tests" / "hidden")

    # 3 and 4. Reviews: the mechanical ones first (no model), then the inspector's code review.
    flags: list[Flag] = review_build_log(BuildLog(run_dir / "build.log").read()) + review_workspace(workspace)
    bundle = build_bundle(run_dir, ctx.config.inspector_max_chars)
    usage = Usage()
    report = None
    review_note = ""
    try:
        report = review_workspace_with_model(bundle, make_chat_model(ctx), usage)
    except LLMBadOutput as exc:
        review_note = f"inspector review unavailable: {exc}"
    # An LLMError propagates: the pipeline node records it as error/api_error without advancing the
    # stage, so no verdict is written and a re-run repeats the whole inspection.
    finally:
        record.budget.spent_usd += usage.cost_usd
        record.budget.spent_tokens += usage.input_tokens + usage.output_tokens
    confidence: float | None = None
    if report is not None:
        flags += to_flags(report)
        confidence = report.confidence
        review_note = report.summary

    outcome = decide(mismatches, hidden, flags)

    parts = [f"hidden tests: {len(hidden.passed)} passed, {len(hidden.failed)} failed"]
    if hidden.timed_out:
        parts.append("hidden test run timed out")
    if hidden.altered:
        parts.append(f"{len(hidden.altered)} hidden test outcome(s) were altered in-process by workspace code")
    if "workspace" in mismatches:
        parts.append("workspace differs from the tree that passed the public tests")
    scope_mismatches = [m for m in mismatches if m != "workspace"]
    if scope_mismatches:
        parts.append(f"scope integrity violated: {', '.join(scope_mismatches)}")
    if flags:
        parts.append(f"{len(flags)} flag(s): " + ", ".join(sorted({f.kind for f in flags})))
    if bundle.truncated:
        parts.append(f"inspector input truncated: {len(bundle.truncated)} item(s)")
    parts.append(review_note)

    Verdict(
        outcome=outcome,
        integrity_mismatches=list(mismatches),
        hidden_passed=list(hidden.passed),
        hidden_failed=list(hidden.failed),
        flags=flags,
        summary="; ".join(p for p in parts if p),
        confidence=confidence,
    ).write(run_dir)
    record.outcome = outcome
