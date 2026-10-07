"""Live: a real Claude agent builds the canary under the Max subscription. Opt in with
PAPER2CODE_LIVE_BUILD=1. Costs subscription usage (a few minutes of a session), no API dollars."""
import os

import pytest

from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord

pytestmark = pytest.mark.skipif(os.environ.get("PAPER2CODE_LIVE_BUILD") != "1", reason="set PAPER2CODE_LIVE_BUILD=1 to run a real agent")


def test_live_agent_builds_the_canary(tmp_path, canary_dir, capsys):
    assert main(["init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001", "--title", "EMA denoising canary",
                 "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "agent", "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    events = BuildLog(run_dir / "build.log").read()
    assert any(e["event"] == "session_init" for e in events)
    assert rec.outcome in (Outcome.COMPLETED, Outcome.COMPLETED_SUSPICIOUS, Outcome.HIDDEN_FAILED, Outcome.INCOMPLETE_STUCK, Outcome.INCOMPLETE_BUDGET), rec.outcome
    assert rec.budget.spent_tokens > 0
    print("outcome:", rec.outcome, "| test runs:", rec.counters.test_runs_used, "| tokens:", rec.budget.spent_tokens,
          "| events:", [e["event"] for e in events])
