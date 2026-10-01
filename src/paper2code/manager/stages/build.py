"""Build stage: the manager drives a builder and is the only writer of build.log and test history."""
from __future__ import annotations

from paper2code.agents.builder.base import BuildContext, Builder, BuildFinished
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.sandbox.runner import TestRunner, TestRunResult

ALL_PUBLIC_PASSED = "all_public_passed"
GIVE_UP = "give_up"
BUILDER_RETURNED = "builder_returned"


class BuildSession:
    """Manager-owned. Counts test runs, writes build.log, and decides when the session is over."""

    def __init__(self, record: RunRecord, runner: TestRunner, log: BuildLog) -> None:
        self.record = record
        self.runner = runner
        self.log = log
        self.workspace = record.run_dir / "workspace"
        self.public_tests = record.run_dir / "scope" / "tests" / "public"
        self.finished = False
        self.finish_reason: str | None = None

    def run_tests(self) -> TestRunResult:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        result = self.runner.run(self.workspace, self.public_tests)
        self.record.counters.test_runs_used += 1
        self.record.counters.attempts += 1
        self.record.budget.gpu_seconds += result.gpu_seconds
        if result.all_passed:
            # Anchor the exact tree that passed; the inspector refuses a workspace that drifted.
            self.record.workspace_sha256 = result.workspace_sha256
        self.record.save()
        self.log.append({
            "event": "run_tests",
            "call": self.record.counters.test_runs_used,
            "passed": list(result.passed),
            "failed": list(result.failed),
            "timed_out": result.timed_out,
            "duration_s": round(result.duration_s, 3),
            "gpu_seconds": result.gpu_seconds,
        })
        if result.all_passed:
            self.finish(ALL_PUBLIC_PASSED)
        return result

    def give_up(self, reason: str) -> None:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        self.log.append({"event": "give_up", "reason": reason})
        self.finish(GIVE_UP)

    def finish(self, reason: str) -> None:
        self.finished = True
        self.finish_reason = reason


def run_with_builder(record: RunRecord, ctx: RunContext, builder: Builder) -> None:
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    log = BuildLog(run_dir / "build.log")
    session = BuildSession(record, make_runner(ctx), log)
    build_ctx = BuildContext(
        workspace=workspace,
        spec_path=scope / "spec.md",
        interface_path=scope / "interface.md",
        public_tests=scope / "tests" / "public",
        run_tests=session.run_tests,
        give_up=session.give_up,
    )
    log.append({"event": "session_start", "builder": ctx.builder})
    try:
        builder.build(build_ctx)
    except BuildFinished:
        pass
    except Exception as exc:
        log.append({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
    if not session.finished:
        session.finish(BUILDER_RETURNED)
    log.append({"event": "session_end", "reason": session.finish_reason})
    if session.finish_reason != ALL_PUBLIC_PASSED:
        record.outcome = Outcome.INCOMPLETE_STUCK
    record.save()


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.agents.builder.factory import make_builder

    run_with_builder(record, ctx, make_builder(ctx))
