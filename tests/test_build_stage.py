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
