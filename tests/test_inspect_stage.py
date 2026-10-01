import shutil
from datetime import date

from paper2code.config import Config
from paper2code.manager.freeze import write_manifest
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, create_run
from paper2code.manager.verdict import Verdict


def _seed_built_run(tmp_path, canary_dir, implementation):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    write_manifest(rec.run_dir / "scope")
    shutil.copytree(canary_dir / implementation, rec.run_dir / "workspace")
    rec.stage = "build"
    rec.save()
    return rec


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120))


def test_reference_workspace_is_completed(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.COMPLETED
    assert final.stage == "inspect"
    v = Verdict.load(rec.run_dir)
    assert v.outcome is Outcome.COMPLETED
    assert v.integrity_mismatches == []
    assert len(v.hidden_passed) == 5
    assert v.hidden_failed == []
    assert v.flags == []
    assert v.summary


def test_hardcoded_workspace_is_hidden_failed(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "hardcoded")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.HIDDEN_FAILED
    v = Verdict.load(rec.run_dir)
    assert len(v.hidden_failed) == 5


def test_edited_hidden_test_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    target = rec.run_dir / "scope" / "tests" / "hidden" / "test_claim_hidden.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED
    v = Verdict.load(rec.run_dir)
    assert v.integrity_mismatches == ["tests/hidden/test_claim_hidden.py"]
    assert len(v.hidden_passed) == 5  # hidden tests still run and are recorded


def test_added_public_conftest_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    (rec.run_dir / "scope" / "tests" / "public" / "conftest.py").write_text("", encoding="utf-8")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED
