from datetime import date

from paper2code.config import Config
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, Paper, RunError, create_run
from paper2code.manager.verdict import Verdict


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path))


def test_summary_after_completed_run(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.paper = Paper("canary-0001", "EMA denoising canary", "https://example.invalid/canary")
    rec.stage = "inspect"
    rec.outcome = Outcome.COMPLETED
    rec.counters.test_runs_used = 3
    rec.budget.gpu_seconds = 42.0
    rec.budget.spent_usd = 1.25
    rec.save()
    Verdict(outcome=Outcome.COMPLETED, hidden_passed=["h::a"], summary="hidden tests: 1 passed, 0 failed").write(rec.run_dir)

    final = run_stage("report", rec.run_dir, _ctx(tmp_path))
    assert final.stage == "report"
    assert final.finished_at is not None
    text = (rec.run_dir / "summary.md").read_text(encoding="utf-8")
    assert "EMA denoising canary" in text
    assert "canary-0001" in text
    assert "completed" in text
    assert "3 of 25" in text
    assert "1.25" in text
    assert "hidden tests: 1 passed, 0 failed" in text


def test_summary_after_incomplete_run_without_verdict(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "build"
    rec.outcome = Outcome.INCOMPLETE_STUCK
    rec.save()
    run_stage("report", rec.run_dir, _ctx(tmp_path))
    text = (rec.run_dir / "summary.md").read_text(encoding="utf-8")
    assert "incomplete_stuck" in text
    assert "no verdict" in text


def test_summary_after_error_run(tmp_path):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    rec.stage = "build"
    rec.outcome = Outcome.ERROR
    rec.error = RunError("build", "rate_limited", "weekly cap hit")
    rec.save()
    run_stage("report", rec.run_dir, _ctx(tmp_path))
    text = (rec.run_dir / "summary.md").read_text(encoding="utf-8")
    assert "rate_limited" in text
    assert "weekly cap hit" in text
