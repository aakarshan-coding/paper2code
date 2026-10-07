"""Cap enforcement for the build loop (spec 9.3). Pure functions; the session calls them."""
from __future__ import annotations

from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord

TEST_RUNS_CAP = "test_runs_cap"
WALL_CLOCK_CAP = "wall_clock_cap"
GPU_BUDGET_CAP = "gpu_budget_cap"
STALL = "stall"
ALL_PUBLIC_PASSED = "all_public_passed"
GIVE_UP = "give_up"
BUILDER_RETURNED = "builder_returned"

_OUTCOMES: dict[str, Outcome | None] = {
    ALL_PUBLIC_PASSED: None,
    TEST_RUNS_CAP: Outcome.INCOMPLETE_BUDGET,
    WALL_CLOCK_CAP: Outcome.INCOMPLETE_BUDGET,
    GPU_BUDGET_CAP: Outcome.INCOMPLETE_BUDGET,
    GIVE_UP: Outcome.INCOMPLETE_STUCK,
    STALL: Outcome.INCOMPLETE_STUCK,
    BUILDER_RETURNED: Outcome.INCOMPLETE_STUCK,
}


def gpu_cost_usd(gpu_seconds: float, gpu_usd_per_hour: float) -> float:
    return gpu_seconds * gpu_usd_per_hour / 3600.0


def cap_before_run(record: RunRecord, elapsed_s: float, gpu_usd_per_hour: float) -> str | None:
    """The cap that forbids another run_tests call now, or None."""
    if record.counters.test_runs_used >= record.caps.test_runs:
        return TEST_RUNS_CAP
    if elapsed_s >= record.caps.wall_clock_s:
        return WALL_CLOCK_CAP
    if gpu_cost_usd(record.budget.gpu_seconds, gpu_usd_per_hour) >= record.budget.limit_usd:
        return GPU_BUDGET_CAP
    return None


def stalled(failing_history: list[frozenset[str]], stall_n: int) -> bool:
    """The last stall_n runs failed on exactly the same non-empty set of tests."""
    if stall_n <= 0 or len(failing_history) < stall_n:
        return False
    tail = failing_history[-stall_n:]
    return bool(tail[0]) and all(s == tail[0] for s in tail)


def outcome_for(finish_reason: str) -> Outcome | None:
    return _OUTCOMES[finish_reason]
