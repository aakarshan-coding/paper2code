"""Spec section 15: the canary scope must reach `completed`; adversarial canaries must be caught."""
from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import Verdict


def _init(tmp_path, canary_dir):
    rc = main([
        "init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001",
        "--title", "EMA denoising canary", "--runs-root", str(tmp_path), "--date", "2026-09-30",
    ])
    assert rc == 0
    return tmp_path / "2026-09-30"


def _run(run_dir, reference):
    return main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(reference)])


def test_canary_reaches_completed(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    assert _run(run_dir, canary_dir / "reference") == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.COMPLETED
    assert rec.stage == "report"
    assert rec.finished_at is not None
    assert rec.counters.test_runs_used == 1
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "build.log", "run.json", "scope", "summary.md", "verdict.json", "workspace",
    ]
    assert Verdict.load(run_dir).outcome is Outcome.COMPLETED
    assert "completed" in (run_dir / "summary.md").read_text(encoding="utf-8")


def test_adversarial_hardcoded_workspace_is_hidden_failed(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    assert _run(run_dir, canary_dir / "hardcoded") == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.HIDDEN_FAILED
    # the builder saw all public tests pass, so the build stage itself ended cleanly
    assert BuildLog(run_dir / "build.log").events("session_end")[0]["reason"] == "all_public_passed"


def test_adversarial_modified_test_file_is_tests_tampered(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    target = run_dir / "scope" / "tests" / "public" / "test_claim.py"
    target.write_text(target.read_text(encoding="utf-8").replace("0.5 *", "5.0 *"), encoding="utf-8")
    assert _run(run_dir, canary_dir / "reference") == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.TESTS_TAMPERED
    assert Verdict.load(run_dir).integrity_mismatches == ["tests/public/test_claim.py"]


def test_adversarial_builder_that_never_passes_is_incomplete_stuck(tmp_path, canary_dir):
    run_dir = _init(tmp_path, canary_dir)
    empty_reference = tmp_path / "empty"
    empty_reference.mkdir()
    assert _run(run_dir, empty_reference) == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.INCOMPLETE_STUCK
    assert not (run_dir / "verdict.json").exists()
    assert "no verdict" in (run_dir / "summary.md").read_text(encoding="utf-8")
