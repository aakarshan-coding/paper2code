"""Build stage: the manager drives a builder and is the only writer of build.log and test history."""
from __future__ import annotations

import time

from paper2code.agents.builder.base import BuildContext, Builder, BuildFinished
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.caps import (
    ALL_PUBLIC_PASSED,
    BUILDER_RETURNED,
    GIVE_UP,
    STALL,
    WALL_CLOCK_CAP,
    cap_before_run,
    outcome_for,
    stalled,
)
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunError, RunRecord
from paper2code.sandbox.runner import TestRunner, TestRunResult


def _elapsed_since_first_start(log: BuildLog) -> float:
    from datetime import datetime, timezone

    starts = log.events("session_start")
    if not starts:
        return 0.0
    try:
        first = datetime.fromisoformat(starts[0]["ts"])
    except (KeyError, ValueError):
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - first).total_seconds())


class BuildSession:
    """Manager-owned. Counts test runs, enforces caps, writes build.log, decides when the session is over."""

    def __init__(
        self, record: RunRecord, runner: TestRunner, log: BuildLog, gpu_usd_per_hour: float = 1.0, now=time.monotonic,
    ) -> None:
        self.record = record
        self.runner = runner
        self.log = log
        self.gpu_usd_per_hour = gpu_usd_per_hour
        self.now = now
        # Resume: the wall clock started at the FIRST session_start of this run, not at this process's start,
        # so a crash loop cannot grant a fresh two hours every time.
        self.started_at = now() - _elapsed_since_first_start(log)
        self.workspace = record.run_dir / "workspace"
        self.public_tests = record.run_dir / "scope" / "tests" / "public"
        self.finished = False
        self.finish_reason: str | None = None
        self.last_result: TestRunResult | None = None
        # Resume: the stall window continues across a crash because build.log is append-only.
        self.failing_history: list[frozenset[str]] = [
            frozenset(e.get("failed", [])) for e in log.events("run_tests")
        ]

    @property
    def elapsed_s(self) -> float:
        return self.now() - self.started_at

    def run_tests(self) -> TestRunResult:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        cap = cap_before_run(self.record, self.elapsed_s, self.gpu_usd_per_hour)
        if cap is not None:
            self.finish(cap)
            raise BuildFinished(cap)
        result = self.runner.run(self.workspace, self.public_tests)
        self.last_result = result
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
            "elapsed_s": round(self.elapsed_s, 1),
        })
        self.failing_history.append(result.failing_set)
        if result.all_passed:
            self.finish(ALL_PUBLIC_PASSED)
        elif stalled(self.failing_history, self.record.caps.stall_n):
            self.finish(STALL)
        return result

    def give_up(self, reason: str) -> None:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        self.log.append({"event": "give_up", "reason": reason})
        self.finish(GIVE_UP)

    def check_wall_clock(self) -> bool:
        """Finish with the wall-clock cap if time is up. Called by the agent driver's hook."""
        if not self.finished and self.elapsed_s >= self.record.caps.wall_clock_s:
            self.finish(WALL_CLOCK_CAP)
            return True
        return False

    def finish(self, reason: str) -> None:
        self.finished = True
        self.finish_reason = reason


def run_with_builder(record: RunRecord, ctx: RunContext, builder: Builder) -> None:
    from paper2code.agents.builder.agent import RateLimited
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    log = BuildLog(run_dir / "build.log")
    session = BuildSession(record, make_runner(ctx), log, gpu_usd_per_hour=ctx.config.gpu_usd_per_hour)
    build_ctx = BuildContext(
        workspace=workspace,
        spec_path=scope / "spec.md",
        interface_path=scope / "interface.md",
        public_tests=scope / "tests" / "public",
        run_tests=session.run_tests,
        give_up=session.give_up,
        session=session,
    )
    log.append({"event": "session_start", "builder": ctx.builder, "test_runs_used": record.counters.test_runs_used})
    try:
        builder.build(build_ctx)
    except BuildFinished:
        pass
    except RateLimited as exc:
        # Infrastructure outcome, not the builder's failure: keep workspace and log as they stand, no retry today.
        log.append({"event": "session_end", "reason": "rate_limited", "elapsed_s": round(session.elapsed_s, 1)})
        record.outcome = Outcome.ERROR
        record.error = RunError(stage="build", reason="rate_limited", message=str(exc))
        record.save()
        return
    except Exception as exc:
        log.append({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
    if not session.finished:
        session.finish(BUILDER_RETURNED)
    log.append({"event": "session_end", "reason": session.finish_reason, "elapsed_s": round(session.elapsed_s, 1)})
    outcome = outcome_for(session.finish_reason)
    if outcome is not None:
        record.outcome = outcome
    record.save()


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.agents.builder.factory import make_builder

    run_with_builder(record, ctx, make_builder(ctx))
