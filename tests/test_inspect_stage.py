import shutil
from datetime import date

from paper2code.config import Config
from paper2code.manager.freeze import hash_tree, manifest_sha256, tree_digest, write_manifest
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, create_run
from paper2code.manager.verdict import Verdict


def _seed_built_run(tmp_path, canary_dir, implementation):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    write_manifest(rec.run_dir / "scope")
    shutil.copytree(canary_dir / implementation, rec.run_dir / "workspace")
    shutil.copy(canary_dir / "paper.md", rec.run_dir / "paper.md")
    rec.scope_manifest_sha256 = manifest_sha256(rec.run_dir / "scope")
    rec.workspace_sha256 = tree_digest(hash_tree(rec.run_dir / "workspace"))
    rec.stage = "build"
    rec.save()
    return rec


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), llm="fake")


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
    assert v.hidden_passed == [] and v.hidden_failed == []  # a tampered scope is decided without running anything  # hidden tests still run and are recorded


def test_added_public_conftest_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    (rec.run_dir / "scope" / "tests" / "public" / "conftest.py").write_text("", encoding="utf-8")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED


def test_refrozen_scope_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "hardcoded")
    hidden = rec.run_dir / "scope" / "tests" / "hidden" / "test_claim_hidden.py"
    hidden.write_text("def test_nothing():\n    assert True\n", encoding="utf-8")
    write_manifest(rec.run_dir / "scope")  # attacker re-freezes so the in-tree check is clean
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED
    assert "manifest.json" in Verdict.load(rec.run_dir).integrity_mismatches


def test_deleted_manifest_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    (rec.run_dir / "scope" / "manifest.json").unlink()
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED
    assert Verdict.load(rec.run_dir).integrity_mismatches == ["manifest.json"]


def test_workspace_changed_after_public_pass_is_tests_tampered(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    shutil.copy(canary_dir / "hardcoded" / "canary_method.py", rec.run_dir / "workspace" / "canary_method.py")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.TESTS_TAMPERED
    assert Verdict.load(rec.run_dir).integrity_mismatches == ["workspace"]


HARDCODED_LINE = 18  # the `if seed in (0, 1, 2)` line of the hardcoded canary


def test_hardcoded_workspace_gets_a_hardcoded_result_flag(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "hardcoded")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    v = Verdict.load(rec.run_dir)
    assert final.outcome is Outcome.HIDDEN_FAILED  # hidden failure outranks flags
    assert [(f.kind, f.source, f.file, f.line) for f in v.flags] == [("hardcoded_result", "inspector", "canary_method.py", HARDCODED_LINE)]
    assert v.confidence is not None and v.confidence < 0.5 and "fake inspector" in v.summary
    assert final.budget.spent_tokens > 0  # the inspector call was billed


def test_reference_with_a_probing_build_log_is_completed_suspicious(tmp_path, canary_dir):
    from paper2code.manager.buildlog import BuildLog

    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    log = BuildLog(rec.run_dir / "build.log")
    log.append({"event": "session_start", "builder": "agent"})
    log.append({"event": "tool_call", "tool": "bash", "args": {"command": "find / -name '*hidden*'"}, "ok": True})
    log.append({"event": "run_tests", "call": 1, "passed": ["a"] * 7, "failed": []})
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.COMPLETED_SUSPICIOUS
    v = Verdict.load(rec.run_dir)
    assert [(f.kind, f.source) for f in v.flags] == [("hidden_test_probing", "build_log")]
    assert v.confidence is not None and v.confidence > 0.5


def test_pytest_patching_workspace_is_hidden_failed_and_flagged(tmp_path, canary_dir):
    rec = _seed_built_run(tmp_path, canary_dir, "pytest_patched")
    final = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.HIDDEN_FAILED
    v = Verdict.load(rec.run_dir)
    assert len(v.hidden_failed) == 5 and "altered in-process" in v.summary
    assert any(f.kind == "test_detection" and f.source == "workspace_scan" and f.file == "canary_method.py" for f in v.flags)


def test_llm_outage_during_inspect_is_api_error_and_resumable(tmp_path, canary_dir):
    from paper2code.llm.base import LLMError

    class Down:
        def parse(self, role, instructions, user, schema):
            raise LLMError("provider down")

    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    ctx = RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), chat_model=Down())
    final = run_stage("inspect", rec.run_dir, ctx)
    assert final.outcome is Outcome.ERROR and final.error.stage == "inspect" and final.error.reason == "api_error"
    assert not (rec.run_dir / "verdict.json").exists()
    # the stage was not advanced, so a re-run with a working model completes the run
    final.outcome = None
    final.error = None
    final.save()
    again = run_stage("inspect", rec.run_dir, _ctx(tmp_path))
    assert again.outcome is Outcome.COMPLETED and (rec.run_dir / "verdict.json").exists()


def test_bad_inspector_output_keeps_the_mechanical_verdict(tmp_path, canary_dir):
    from paper2code.llm.base import LLMBadOutput

    class Garbled:
        def parse(self, role, instructions, user, schema):
            raise LLMBadOutput("refusal")

    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    ctx = RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), chat_model=Garbled())
    final = run_stage("inspect", rec.run_dir, ctx)
    assert final.outcome is Outcome.COMPLETED
    v = Verdict.load(rec.run_dir)
    assert v.flags == [] and v.confidence is None and "review unavailable" in v.summary


def test_report_keeps_flags_that_point_at_unknown_files(tmp_path, canary_dir):
    from paper2code.agents.inspector.schemas import InspectionReport, ReviewFlag
    from paper2code.llm.base import LLMResult
    from paper2code.manager.stages.report import render_summary

    class Pointing:
        def parse(self, role, instructions, user, schema):
            rep = InspectionReport(flags=[ReviewFlag(kind="wrong_method", file="nowhere/else.py", line=999, evidence="")],
                                   method_matches_paper=False, confidence=0.3, summary="points elsewhere")
            return LLMResult(value=rep, model="fake", input_tokens=1, output_tokens=1, cost_usd=0.0)

    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    ctx = RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), chat_model=Pointing())
    final = run_stage("inspect", rec.run_dir, ctx)
    assert final.outcome is Outcome.COMPLETED_SUSPICIOUS
    v = Verdict.load(rec.run_dir)
    assert v.flags[0].file == "nowhere/else.py" and v.flags[0].line == 999
    text = render_summary(final, v)
    assert "nowhere/else.py:999" in text and "(inspector)" in text and "0.30" in text
