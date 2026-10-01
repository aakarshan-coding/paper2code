import json
from datetime import date

import pytest

from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import (
    STAGES,
    Caps,
    Paper,
    RunError,
    RunRecord,
    create_run,
    stage_index,
)

TODAY = date(2026, 9, 30)


def test_outcome_values_match_spec():
    assert {o.value for o in Outcome} == {
        "completed", "completed_suspicious", "hidden_failed", "tests_tampered",
        "incomplete_budget", "incomplete_stuck", "scope_rejected", "no_candidates", "error",
    }


def test_stage_order():
    assert STAGES == ("fetch", "score", "select", "scope", "build", "inspect", "report")
    assert stage_index(None) == -1
    assert stage_index("fetch") == 0
    assert stage_index("report") == 6


def test_create_run_writes_run_json_with_spec_fields(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(), limit_usd=10.0)
    assert rec.run_dir == tmp_path / "2026-09-30"
    data = json.loads((rec.run_dir / "run.json").read_text(encoding="utf-8"))
    assert set(data) == {
        "run_id", "started_at", "finished_at", "stage", "outcome", "paper", "policy",
        "budget", "caps", "counters", "error",
    }
    assert data["run_id"] == "2026-09-30"
    assert data["stage"] is None
    assert data["outcome"] is None
    assert data["error"] is None
    assert set(data["paper"]) == {"arxiv_id", "title", "url"}
    assert set(data["policy"]) == {"name", "version"}
    assert set(data["budget"]) == {"limit_usd", "spent_usd", "spent_tokens", "gpu_seconds"}
    assert set(data["caps"]) == {"test_runs", "wall_clock_s", "stall_n"}
    assert set(data["counters"]) == {"test_runs_used", "attempts"}
    assert data["budget"]["limit_usd"] == 10.0
    assert data["caps"]["test_runs"] == 25


def test_second_run_same_day_gets_suffix(tmp_path):
    first = create_run(tmp_path, TODAY, Caps(), 10.0)
    (first.run_dir / "marker").write_text("keep me", encoding="utf-8")
    second = create_run(tmp_path, TODAY, Caps(), 10.0)
    third = create_run(tmp_path, TODAY, Caps(), 10.0)
    assert second.run_dir == tmp_path / "2026-09-30-2"
    assert third.run_dir == tmp_path / "2026-09-30-3"
    assert (first.run_dir / "marker").read_text(encoding="utf-8") == "keep me"


def test_save_and_load_roundtrip(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(test_runs=3), 5.0)
    rec.stage = "build"
    rec.outcome = Outcome.INCOMPLETE_STUCK
    rec.paper = Paper(arxiv_id="2509.12345", title="T", url="https://arxiv.org/abs/2509.12345")
    rec.counters.test_runs_used = 2
    rec.budget.gpu_seconds = 12.5
    rec.error = RunError(stage="build", reason="rate_limited", message="window exhausted")
    rec.save()

    loaded = RunRecord.load(rec.run_dir)
    assert loaded.run_dir == rec.run_dir
    assert loaded.stage == "build"
    assert loaded.outcome is Outcome.INCOMPLETE_STUCK
    assert loaded.paper.arxiv_id == "2509.12345"
    assert loaded.counters.test_runs_used == 2
    assert loaded.budget.gpu_seconds == 12.5
    assert loaded.caps.test_runs == 3
    assert loaded.error == RunError("build", "rate_limited", "window exhausted")


def test_is_done_compares_stage_order(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(), 10.0)
    assert not rec.is_done("fetch")
    rec.stage = "scope"
    assert rec.is_done("fetch")
    assert rec.is_done("scope")
    assert not rec.is_done("build")


def test_save_leaves_no_temp_file(tmp_path):
    rec = create_run(tmp_path, TODAY, Caps(), 10.0)
    rec.save()
    assert sorted(p.name for p in rec.run_dir.iterdir()) == ["run.json"]


def test_load_missing_run_json_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        RunRecord.load(tmp_path / "nope")
