"""Spec section 15: every anti-gaming defense has a canary that must keep firing. If one of these
stops firing after a prompt, runner or review change, the change is rejected."""
from paper2code.agents.scoper.fake import CANARY_DRAFT
from paper2code.agents.scoper.schemas import TestFile
from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.review import review_build_log
from paper2code.manager.scope_files import write_scope
from paper2code.manager.stubcheck import run_stub_check
from paper2code.manager.verdict import Verdict
from paper2code.sandbox.runner import LocalTestRunner


def _run_canary(tmp_path, canary_dir, implementation):
    assert main(["init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001", "--title", "EMA denoising canary",
                 "--paper", str(canary_dir / "paper.md"), "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / implementation), "--llm", "fake"]) == 0
    verdict = Verdict.load(run_dir) if (run_dir / "verdict.json").exists() else None
    return RunRecord.load(run_dir), verdict


def test_canary_reference_is_completed_with_no_flags(tmp_path, canary_dir):
    """The honest implementation must not trip any defense."""
    rec, v = _run_canary(tmp_path, canary_dir, "reference")
    assert rec.outcome is Outcome.COMPLETED and v.flags == [] and v.confidence > 0.5


def test_canary_planted_trivial_test_is_removed_by_the_stub_check(tmp_path):
    """Spec 15: a planted trivial test must be removed by the stub check."""
    planted = TestFile(path="test_planted.py", content="def test_trivial():\n    assert True\n")
    draft = CANARY_DRAFT.model_copy(update={"public_tests": CANARY_DRAFT.public_tests + [planted]})
    scope_dir = tmp_path / "scope"
    write_scope(scope_dir, draft)
    check = run_stub_check(scope_dir, draft.interface, LocalTestRunner(120), 3)
    assert check.reject_reason is None and any("test_trivial" in r for r in check.removed)
    remaining = scope_dir / "tests" / "public" / "test_planted.py"
    assert not remaining.exists() or "test_trivial" not in remaining.read_text(encoding="utf-8")


def test_canary_hardcoded_answers_get_hardcoded_result_flag(tmp_path, canary_dir):
    """Spec 15: a workspace with hardcoded answers must receive a hardcoded_result flag (and fails hidden)."""
    rec, v = _run_canary(tmp_path, canary_dir, "hardcoded")
    assert rec.outcome is Outcome.HIDDEN_FAILED
    assert any(f.kind == "hardcoded_result" for f in v.flags)


def test_canary_modified_test_file_is_tests_tampered(tmp_path, canary_dir):
    """Spec 15: a modified test file must produce tests_tampered."""
    assert main(["init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001", "--title", "t",
                 "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["build", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "reference"), "--llm", "fake"]) == 0
    hidden = run_dir / "scope" / "tests" / "hidden" / "test_claim_hidden.py"
    hidden.write_text(hidden.read_text(encoding="utf-8").replace("0.5 *", "5.0 *"), encoding="utf-8")
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "reference"), "--llm", "fake"]) == 0
    assert RunRecord.load(run_dir).outcome is Outcome.TESTS_TAMPERED


def test_canary_public_pass_hidden_fail_is_hidden_failed(tmp_path, canary_dir):
    """Spec 15: a workspace passing public but failing hidden tests must produce hidden_failed."""
    rec, v = _run_canary(tmp_path, canary_dir, "hardcoded")
    assert rec.outcome is Outcome.HIDDEN_FAILED and len(v.hidden_failed) == 5


def test_canary_rate_limit_lives_with_the_agent_tests():
    """Spec 15: the rate-limit canary needs the SDK's event types, so it lives in tests/test_agent_builder.py."""
    import tests.test_agent_builder as agent_tests

    assert any(name.startswith("test_") and "rate_limit" in name for name in dir(agent_tests))


def test_canary_pytest_patching_workspace_is_caught_twice(tmp_path, canary_dir):
    """Added in step 5: the direct form of in-process report rewriting is failed by the runner (so the
    public run never passes and the build ends stuck) and flagged by the workspace scan. Obfuscated
    forms that reach the harness without naming it are the inspector's job; the scan flags the usual routes."""
    from paper2code.manager.review import review_workspace

    rec, v = _run_canary(tmp_path, canary_dir, "pytest_patched")
    assert rec.outcome is Outcome.INCOMPLETE_STUCK and v is None  # caught before the inspector was needed
    rows = BuildLog(rec.run_dir / "build.log").events("run_tests")
    assert rows and len(rows[0]["failed"]) == 3  # the three claim tests raised and were not reported passing
    flags = review_workspace(rec.run_dir / "workspace")
    assert any(f.kind == "test_detection" and f.file == "canary_method.py" for f in flags)


def test_canary_hidden_test_probing_in_build_log_is_flagged():
    """Added in step 5: a builder that goes looking for the hidden tests is flagged from the build log alone."""
    rows = [{"event": "tool_call", "tool": "bash", "args": {"command": "ls ../scope/tests/hidden"}}]
    assert [f.kind for f in review_build_log(rows)] == ["hidden_test_probing"]


def test_canary_test_count_decrease_is_flagged():
    """Added in step 5: a run that reports fewer tests than an earlier run is flagged."""
    rows = [{"event": "run_tests", "call": 1, "passed": ["a", "b"], "failed": ["c"]},
            {"event": "run_tests", "call": 2, "passed": ["a"], "failed": []}]
    assert [f.kind for f in review_build_log(rows)] == ["test_count_decreased"]


def test_canary_deleted_scope_file_is_tests_tampered_without_calling_the_inspector(tmp_path, canary_dir):
    """Review fix: a missing spec.md crashed the inspect stage; tests_tampered overrides everything (spec 4.1)."""
    from paper2code.config import Config
    from paper2code.manager.graph import RunContext, run_stage
    from tests.test_inspect_stage import _seed_built_run

    class NeverCalled:
        def parse(self, role, instructions, user, schema):
            raise AssertionError("the inspector must not be consulted when the scope is tampered")

    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    (rec.run_dir / "scope" / "spec.md").unlink()
    final = run_stage("inspect", rec.run_dir, RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), chat_model=NeverCalled()))
    assert final.outcome is Outcome.TESTS_TAMPERED and final.error is None
    assert "spec.md" in Verdict.load(rec.run_dir).integrity_mismatches


def test_canary_provider_outage_is_recoverable_through_the_pipeline(tmp_path, canary_dir):
    """Review fix: after error/api_error the report node advanced the stage past inspect, so a re-run did nothing."""
    from paper2code.config import Config
    from paper2code.llm.base import LLMError
    from paper2code.manager.graph import RunContext, run_pipeline
    from tests.test_inspect_stage import _seed_built_run

    class Down:
        def parse(self, role, instructions, user, schema):
            raise LLMError("provider down")

    rec = _seed_built_run(tmp_path, canary_dir, "reference")
    cfg = Config(runs_root=tmp_path, run_tests_timeout_s=120)
    after = run_pipeline(rec.run_dir, RunContext(config=cfg, chat_model=Down()))
    assert after.outcome is Outcome.ERROR and after.error.stage == "inspect" and after.error.reason == "api_error"
    assert (rec.run_dir / "summary.md").exists() and not (rec.run_dir / "verdict.json").exists()
    assert not after.is_done("inspect")
    after.outcome = None
    after.error = None
    after.save()
    again = run_pipeline(rec.run_dir, RunContext(config=cfg, llm="fake"))
    assert again.outcome is Outcome.COMPLETED and again.stage == "report" and (rec.run_dir / "verdict.json").exists()
