import shutil
from datetime import date

import pytest

from paper2code.agents.builder.base import BuildContext, BuildFinished
from paper2code.agents.builder.stub import StubBuilder
from paper2code.config import Config
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.freeze import hash_tree, tree_digest, write_manifest
from paper2code.manager.graph import RunContext, default_stages, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, RunRecord, create_run
from paper2code.manager.stages import build as build_stage


def _seed_run(tmp_path, canary_dir):
    rec = create_run(tmp_path, date(2026, 9, 30), Caps(), 10.0)
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    write_manifest(rec.run_dir / "scope")
    rec.stage = "scope"
    rec.save()
    return rec


def _ctx(tmp_path, reference):
    return RunContext(
        config=Config(runs_root=tmp_path, run_tests_timeout_s=120), builder="stub", reference_dir=reference,
    )


def test_buildlog_appends_jsonl_with_timestamps(tmp_path):
    log = BuildLog(tmp_path / "build.log")
    log.append({"event": "session_start", "builder": "stub"})
    log.append({"event": "run_tests", "call": 1})
    rows = log.read()
    assert [r["event"] for r in rows] == ["session_start", "run_tests"]
    assert all("ts" in r for r in rows)
    assert log.events("run_tests") == [rows[1]]
    assert BuildLog(tmp_path / "absent.log").read() == []


def test_stub_builder_with_reference_reaches_all_public_passed(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    final = run_stage("build", rec.run_dir, _ctx(tmp_path, canary_dir / "reference"))
    assert final.stage == "build"
    assert final.outcome is None
    assert final.counters.test_runs_used == 1
    assert final.counters.attempts == 1
    assert (rec.run_dir / "workspace" / "canary_method.py").exists()
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert [r["event"] for r in rows] == ["session_start", "run_tests", "session_end"]
    assert rows[1]["failed"] == []
    assert len(rows[1]["passed"]) == 7
    assert rows[2]["reason"] == "all_public_passed"


def test_builder_that_never_passes_is_incomplete_stuck(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "canary_method.py").write_text("def ema(xs, alpha):\n    raise NotImplementedError\n", encoding="utf-8")
    final = run_stage("build", rec.run_dir, _ctx(tmp_path, broken))
    assert final.outcome is Outcome.INCOMPLETE_STUCK
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert rows[-1]["event"] == "session_end"
    assert rows[-1]["reason"] == "builder_returned"
    assert rows[1]["failed"]  # the failing public tests were recorded by the manager


def test_give_up_is_incomplete_stuck_with_reason(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)

    class GiveUpBuilder:
        def build(self, ctx: BuildContext) -> None:
            ctx.give_up("cannot find dataset")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path, None), GiveUpBuilder())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.INCOMPLETE_STUCK
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert [r["event"] for r in rows] == ["session_start", "give_up", "session_end"]
    assert rows[1]["reason"] == "cannot find dataset"
    assert rows[2]["reason"] == "give_up"


def test_run_tests_after_finish_raises_build_finished(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    seen = {}

    class GreedyBuilder:
        def build(self, ctx: BuildContext) -> None:
            shutil.copy(canary_dir / "reference" / "canary_method.py", ctx.workspace / "canary_method.py")
            ctx.run_tests()
            try:
                ctx.run_tests()
            except BuildFinished:
                seen["raised"] = True
                raise

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path, None), GreedyBuilder())
    assert seen == {"raised": True}
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is None
    assert final.counters.test_runs_used == 1


def test_stub_builder_copies_reference_tree(tmp_path):
    ref = tmp_path / "ref"
    (ref / "pkg").mkdir(parents=True)
    (ref / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (ref / "main.py").write_text("x = 1\n", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    calls = []
    ctx = BuildContext(
        workspace=ws,
        spec_path=tmp_path / "spec.md",
        interface_path=tmp_path / "interface.md",
        public_tests=tmp_path / "public",
        run_tests=lambda: calls.append("run"),
        give_up=lambda reason: None,
    )
    StubBuilder(ref).build(ctx)
    assert (ws / "main.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (ws / "pkg" / "__init__.py").exists()
    assert calls == ["run"]


def test_public_pass_records_workspace_digest(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    final = run_stage("build", rec.run_dir, _ctx(tmp_path, canary_dir / "reference"))
    assert final.workspace_sha256 == tree_digest(hash_tree(rec.run_dir / "workspace"))


def test_builder_exception_is_logged_and_recorded(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)

    class ExplodingBuilder:
        def build(self, ctx: BuildContext) -> None:
            raise RuntimeError("kaboom")

    stages = {**default_stages(), "build": lambda r, c: build_stage.run_with_builder(r, c, ExplodingBuilder())}
    with pytest.raises(RuntimeError, match="kaboom"):
        run_stage("build", rec.run_dir, _ctx(tmp_path, None), stages)
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert rows[-1]["event"] == "error"
    assert "kaboom" in rows[-1]["message"]
    final = RunRecord.load(rec.run_dir)
    assert final.stage == "scope"  # not advanced: the stage re-runs on resume
    assert final.outcome is None
    assert final.error is not None
    assert (final.error.stage, final.error.reason) == ("build", "exception")
    assert "kaboom" in final.error.message


# ---- step 4a: caps in the build session ----
from paper2code.agents.builder.base import BuildFinished as _BF  # noqa: E402
from paper2code.manager.caps import STALL, TEST_RUNS_CAP, WALL_CLOCK_CAP  # noqa: E402
from paper2code.manager.stages.build import BuildSession  # noqa: E402
from paper2code.sandbox.runner import TestRunResult  # noqa: E402


class _ScriptedRunner:
    """Returns the next scripted result on every run."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def run(self, workspace, tests_dir):
        self.calls += 1
        return self.results.pop(0)


def _fail(*ids):
    return TestRunResult((), tuple(ids), 1, False, 0.5, 2.0, "", "digest")


def _session(tmp_path, canary_dir, runner, now, **caps):
    rec = _seed_run(tmp_path, canary_dir)
    rec.caps = Caps(**{"test_runs": 25, "wall_clock_s": 7200, "stall_n": 5, **caps})
    rec.save()
    (rec.run_dir / "workspace").mkdir(exist_ok=True)
    return rec, BuildSession(rec, runner, BuildLog(rec.run_dir / "build.log"), gpu_usd_per_hour=1.0, now=now)


def test_session_test_runs_cap_finishes_with_budget_reason(tmp_path, canary_dir):
    rec, s = _session(tmp_path, canary_dir, _ScriptedRunner([_fail("a::t")] * 3), now=lambda: 0.0, test_runs=2)
    s.run_tests()
    s.run_tests()
    with pytest.raises(_BF):
        s.run_tests()
    assert s.finished and s.finish_reason == TEST_RUNS_CAP
    assert rec.counters.test_runs_used == 2


def test_session_stall_detection(tmp_path, canary_dir):
    rec, s = _session(tmp_path, canary_dir, _ScriptedRunner([_fail("a::t", "a::u")] * 3), now=lambda: 0.0, stall_n=3)
    s.run_tests()
    s.run_tests()
    s.run_tests()
    assert s.finished and s.finish_reason == STALL
    assert [e["event"] for e in BuildLog(rec.run_dir / "build.log").read()][-1] == "run_tests"


def test_session_wall_clock_cap(tmp_path, canary_dir):
    clock = {"t": 0.0}
    rec, s = _session(tmp_path, canary_dir, _ScriptedRunner([_fail("a::t")] * 2), now=lambda: clock["t"], wall_clock_s=100)
    s.run_tests()
    clock["t"] = 101.0
    assert s.check_wall_clock() is True
    assert s.finish_reason == WALL_CLOCK_CAP
    with pytest.raises(_BF):
        s.run_tests()


def test_session_gpu_budget_cap_uses_gpu_seconds(tmp_path, canary_dir):
    rec, s = _session(tmp_path, canary_dir, _ScriptedRunner([TestRunResult((), ("a::t",), 1, False, 0.5, 7200.0, "", "d")] * 2), now=lambda: 0.0)
    rec.budget.limit_usd = 1.0
    s.run_tests()  # 7200 GPU seconds at 1 USD/h = 2 USD, over the 1 USD limit
    with pytest.raises(_BF):
        s.run_tests()
    assert s.finish_reason == "gpu_budget_cap"


def test_session_resumes_counters_from_record_and_log(tmp_path, canary_dir):
    rec, s = _session(tmp_path, canary_dir, _ScriptedRunner([_fail("a::t")] * 2), now=lambda: 0.0, stall_n=3)
    s.run_tests()
    s.run_tests()
    rec2 = RunRecord.load(rec.run_dir)
    assert rec2.counters.test_runs_used == 2
    s2 = BuildSession(rec2, _ScriptedRunner([_fail("a::t")]), BuildLog(rec.run_dir / "build.log"), now=lambda: 0.0)
    assert len(s2.failing_history) == 2  # seeded from build.log
    s2.run_tests()
    assert s2.finished and s2.finish_reason == STALL  # 3 identical in a row across the crash
    assert rec2.counters.test_runs_used == 3


def test_run_with_builder_maps_cap_reasons_to_outcomes(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    rec.caps = Caps(test_runs=1, wall_clock_s=7200, stall_n=5)
    rec.save()

    class TwoRuns:
        def build(self, ctx):
            ctx.run_tests()
            ctx.run_tests()  # second call hits the cap and raises BuildFinished

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path, None), TwoRuns())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.INCOMPLETE_BUDGET
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert rows[-1]["event"] == "session_end" and rows[-1]["reason"] == TEST_RUNS_CAP and "elapsed_s" in rows[-1]


def test_rate_limit_canary_preserves_workspace_and_log(tmp_path, canary_dir):
    """Spec 15: a simulated subscription rate-limit error during build."""
    from paper2code.agents.builder.agent import RateLimited

    rec = _seed_run(tmp_path, canary_dir)

    class HalfwayThenLimited:
        def build(self, ctx):
            (ctx.workspace / "canary_method.py").write_text("# partial work\n", encoding="utf-8")
            ctx.run_tests()
            raise RateLimited("subscription rate limit reached")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path, None), HalfwayThenLimited())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR
    assert (final.error.stage, final.error.reason) == ("build", "rate_limited")
    assert (rec.run_dir / "workspace" / "canary_method.py").read_text(encoding="utf-8") == "# partial work\n"
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert [r["event"] for r in rows] == ["session_start", "run_tests", "session_end"]
    assert rows[-1]["reason"] == "rate_limited"
    assert final.counters.test_runs_used == 1


def test_make_builder_agent(tmp_path):
    from paper2code.agents.builder.agent import AgentBuilder
    from paper2code.agents.builder.factory import make_builder
    from paper2code.manager.graph import RunContext

    assert isinstance(make_builder(RunContext(config=Config(), builder="agent")), AgentBuilder)


def test_wall_clock_resumes_from_first_session_start(tmp_path, canary_dir):
    from datetime import datetime, timedelta, timezone

    rec = _seed_run(tmp_path, canary_dir)
    rec.caps = Caps(test_runs=25, wall_clock_s=7200, stall_n=5)
    rec.save()
    (rec.run_dir / "workspace").mkdir(exist_ok=True)
    log = BuildLog(rec.run_dir / "build.log")
    three_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    log.append({"ts": three_hours_ago, "event": "session_start", "builder": "agent", "test_runs_used": 0})
    s = BuildSession(rec, _ScriptedRunner([_fail("a::t")]), log, now=lambda: 1000.0)
    assert s.elapsed_s >= 3 * 3600 - 5
    with pytest.raises(_BF):
        s.run_tests()
    assert s.finish_reason == WALL_CLOCK_CAP


def test_run_tests_row_records_errored_and_timed_out(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "canary_method.py").write_text("def ema(xs, alpha:\n", encoding="utf-8")  # syntax error: collection fails
    run_stage("build", rec.run_dir, _ctx(tmp_path, broken))
    row = BuildLog(rec.run_dir / "build.log").events("run_tests")[0]
    assert row["errored"] and row["timed_out"] is False and row["failed"] == row["errored"]
