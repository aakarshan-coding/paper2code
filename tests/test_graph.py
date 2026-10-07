from datetime import date

import pytest

from paper2code.config import Config
from paper2code.manager.graph import RunContext, run_pipeline, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import STAGES, Caps, RunError, RunRecord, create_run


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path))


def _recording_stages(calls, raise_once_at=None, set_outcome_at=None):
    raised = {"done": False}

    def make(name):
        def fn(record, ctx):
            if name == raise_once_at and not raised["done"]:
                raised["done"] = True
                raise RuntimeError(f"boom in {name}")
            calls.append(name)
            if name == set_outcome_at:
                record.outcome = Outcome.SCOPE_REJECTED
        return fn

    return {s: make(s) for s in STAGES}


def test_runs_every_stage_in_order(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    final = run_pipeline(rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == list(STAGES)
    assert final.stage == "report"
    assert RunRecord.load(rec.run_dir).stage == "report"


def test_resume_skips_completed_stages(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "scope"
    rec.save()
    calls = []
    run_pipeline(rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == ["build", "inspect", "report"]


def test_crash_mid_stage_reruns_that_stage(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    stages = _recording_stages(calls, raise_once_at="build")
    with pytest.raises(RuntimeError, match="boom in build"):
        run_pipeline(rec.run_dir, _ctx(tmp_path), stages)
    assert RunRecord.load(rec.run_dir).stage == "scope"
    run_pipeline(rec.run_dir, _ctx(tmp_path), stages)
    assert calls == ["fetch", "score", "select", "scope", "build", "inspect", "report"]


def test_outcome_short_circuits_to_report(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    final = run_pipeline(rec.run_dir, _ctx(tmp_path), _recording_stages(calls, set_outcome_at="scope"))
    assert calls == ["fetch", "score", "select", "scope", "report"]
    assert final.outcome is Outcome.SCOPE_REJECTED
    assert final.stage == "report"


def test_run_stage_runs_only_that_node(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "scope"
    rec.save()
    calls = []
    final = run_stage("build", rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == ["build"]
    assert final.stage == "build"


def test_run_stage_refuses_to_skip_ahead_silently(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    with pytest.raises(ValueError, match="build requires scope"):
        run_stage("build", rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == []


def test_default_stages_before_scope_are_not_implemented_yet(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    with pytest.raises(NotImplementedError):
        run_pipeline(rec.run_dir, _ctx(tmp_path))


def test_run_stage_allows_report_after_outcome_short_circuit(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "build"
    rec.outcome = Outcome.INCOMPLETE_STUCK
    rec.save()
    calls = []
    final = run_stage("report", rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == ["report"]
    assert final.stage == "report"


def test_run_stage_still_refuses_skip_ahead_without_outcome(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "build"
    rec.save()
    calls = []
    with pytest.raises(ValueError, match="report requires inspect"):
        run_stage("report", rec.run_dir, _ctx(tmp_path), _recording_stages(calls))
    assert calls == []


def test_stage_exception_records_error_and_clears_on_success(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    stages = _recording_stages(calls, raise_once_at="build")
    with pytest.raises(RuntimeError):
        run_pipeline(rec.run_dir, _ctx(tmp_path), stages)
    assert RunRecord.load(rec.run_dir).error == RunError("build", "exception", "RuntimeError: boom in build")
    run_pipeline(rec.run_dir, _ctx(tmp_path), stages)
    assert RunRecord.load(rec.run_dir).error is None


def test_until_stops_after_named_stage(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    calls = []
    ctx = RunContext(config=Config(runs_root=tmp_path), until="select")
    final = run_pipeline(rec.run_dir, ctx, _recording_stages(calls))
    assert calls == ["fetch", "score", "select"]
    assert final.stage == "select"
    assert final.outcome is None
