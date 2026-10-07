"""Live Modal tests. Opt in with PAPER2CODE_LIVE_MODAL=1 after `python -m modal deploy
src/paper2code/sandbox/modal_app.py`. Spends a little GPU time and, for the last test, subscription usage."""
import os

import pytest

from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.sandbox.modal_runner import ModalTestRunner

pytestmark = pytest.mark.skipif(os.environ.get("PAPER2CODE_LIVE_MODAL") != "1", reason="set PAPER2CODE_LIVE_MODAL=1 to use Modal")


def test_remote_runner_runs_the_canary_on_the_gpu_function(canary_dir):
    r = ModalTestRunner(timeout_s=300, max_payload_bytes=50 * 2**20).run(canary_dir / "reference", canary_dir / "scope" / "tests" / "hidden")
    assert r.all_passed and len(r.passed) == 5 and r.gpu_seconds > 0
    print("remote hidden suite: passed", len(r.passed), "| gpu_seconds", round(r.gpu_seconds, 1), "| duration", round(r.duration_s, 1))


def test_stub_builder_with_remote_tests_reaches_completed(tmp_path, canary_dir, capsys):
    assert main(["init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001", "--title", "EMA denoising canary",
                 "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--builder", "stub", "--reference", str(canary_dir / "reference"), "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.COMPLETED and rec.budget.gpu_seconds > 0
    print("stub+remote: outcome", rec.outcome, "| gpu_seconds", round(rec.budget.gpu_seconds, 1))


def test_agent_in_sandbox_builds_the_canary(tmp_path, canary_dir, capsys):
    """The full cloud path: agent in a Modal sandbox, tests on the GPU function, hidden tests never in the sandbox."""
    assert main(["init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001", "--title", "EMA denoising canary",
                 "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--builder", "agent", "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    events = BuildLog(run_dir / "build.log").read()
    actions = [e["action"] for e in events if e["event"] == "sandbox"]
    assert actions[0] == "created" and actions[-1] == "terminated"
    assert rec.outcome in (Outcome.COMPLETED, Outcome.COMPLETED_SUSPICIOUS, Outcome.HIDDEN_FAILED, Outcome.INCOMPLETE_STUCK, Outcome.INCOMPLETE_BUDGET), rec
    print("agent+sandbox: outcome", rec.outcome, "| test runs", rec.counters.test_runs_used, "| gpu_seconds", round(rec.budget.gpu_seconds, 1),
          "| tokens", rec.budget.spent_tokens, "| sandbox actions", actions)
