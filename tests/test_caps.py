from datetime import date

import pytest

from paper2code.manager.caps import (
    GPU_BUDGET_CAP,
    STALL,
    TEST_RUNS_CAP,
    WALL_CLOCK_CAP,
    cap_before_run,
    gpu_cost_usd,
    outcome_for,
    stalled,
)
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, create_run


def _rec(tmp_path, **caps):
    return create_run(tmp_path, date(2026, 10, 7), Caps(**caps), limit_usd=1.0)


def test_gpu_cost():
    assert gpu_cost_usd(3600, 1.0) == pytest.approx(1.0)
    assert gpu_cost_usd(0, 5.0) == 0.0


def test_cap_before_run_order(tmp_path):
    rec = _rec(tmp_path, test_runs=2, wall_clock_s=100)
    assert cap_before_run(rec, elapsed_s=0, gpu_usd_per_hour=1.0) is None
    rec.counters.test_runs_used = 2
    assert cap_before_run(rec, 0, 1.0) == TEST_RUNS_CAP
    rec.counters.test_runs_used = 0
    assert cap_before_run(rec, 100, 1.0) == WALL_CLOCK_CAP
    rec.budget.gpu_seconds = 3600  # 1 USD at 1 USD/h against a 1 USD limit
    assert cap_before_run(rec, 0, 1.0) == GPU_BUDGET_CAP
    rec.budget.gpu_seconds = 3599
    assert cap_before_run(rec, 0, 1.0) is None


def test_stalled_requires_n_identical_nonempty_sets():
    a = frozenset({"t::x", "t::y"})
    assert stalled([a, a, a], 3)
    assert not stalled([a, a], 3)
    assert not stalled([a, frozenset({"t::x"}), a], 3)
    assert not stalled([frozenset(), frozenset(), frozenset()], 3)


def test_outcome_for_each_reason():
    assert outcome_for("all_public_passed") is None
    for r in (TEST_RUNS_CAP, WALL_CLOCK_CAP, GPU_BUDGET_CAP):
        assert outcome_for(r) is Outcome.INCOMPLETE_BUDGET
    for r in ("give_up", STALL, "builder_returned"):
        assert outcome_for(r) is Outcome.INCOMPLETE_STUCK
    with pytest.raises(KeyError):
        outcome_for("made_up")


def test_create_run_suffixes_a_second_run_on_the_same_day(tmp_path):
    """Spec 4: a manual rerun the same day gets a numeric suffix (behaviour from step 1, pinned here for step 6)."""
    from paper2code.manager.record import RunRecord

    a = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    b = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    c = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    assert (a.run_id, b.run_id, c.run_id) == ("2026-10-07", "2026-10-07-2", "2026-10-07-3")
    assert b.run_dir == tmp_path / "2026-10-07-2" and RunRecord.load(b.run_dir).run_id == "2026-10-07-2"
