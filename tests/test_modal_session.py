"""The builder sandbox lifetime (create, seed, export, terminate) and the build stage's Modal path,
with the sandbox and the GPU function replaced by local stand-ins."""
import shutil

import pytest

from paper2code.agents.builder.base import BuildContext
from paper2code.config import Config
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.stages import build as build_stage
from paper2code.sandbox.modal_runner import ModalTestRunner
from paper2code.sandbox.modal_session import SandboxFailed, modal_build_session
from paper2code.sandbox.modal_workspace import ModalWorkspace
from paper2code.sandbox.remote_tests import execute_tests
from tests.test_build_stage import _seed_run
from tests.test_modal_workspace import FakeSandbox


def _ctx(tmp_path):
    return RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), no_gpu=False, builder="agent")


def _factory(tmp_path, holder, fail=False):
    def make(config, spec_md, wall_clock_s):
        holder["args"] = (config, spec_md, wall_clock_s)
        if fail:
            raise RuntimeError("no capacity")
        sb = FakeSandbox(tmp_path / "sb")
        holder["sandbox"] = sb
        return sb

    return make


def _runner_cls(timeout_s, max_payload_bytes, snapshot_source=None, app_name="paper2code"):
    """ModalTestRunner with the GPU function replaced by the local executor."""
    return ModalTestRunner(timeout_s=timeout_s, max_payload_bytes=max_payload_bytes, remote=execute_tests, snapshot_source=snapshot_source)


def _workspace_cls(sandbox):
    return ModalWorkspace(sandbox, root=sandbox.work_root())


def _session(rec, tmp_path, log, holder, fail=False):
    return modal_build_session(rec, _ctx(tmp_path), log, sandbox_factory=_factory(tmp_path, holder, fail),
                               workspace_cls=_workspace_cls, runner_cls=_runner_cls)


def test_session_creates_seeds_exports_and_terminates(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    (rec.run_dir / "workspace").mkdir(exist_ok=True)
    (rec.run_dir / "workspace" / "earlier.py").write_text("# from a previous attempt\n", encoding="utf-8")
    holder = {}
    log = BuildLog(rec.run_dir / "build.log")
    with _session(rec, tmp_path, log, holder) as (ws, runner):
        assert ws.read_file("earlier.py") == "# from a previous attempt\n"  # resume seed
        assert ws.read_file(".assignment/spec.md") == (canary_dir / "scope" / "spec.md").read_text(encoding="utf-8")
        assert ".assignment/tests/public/test_claim.py" in ws.list_files()
        src = (canary_dir / "reference" / "canary_method.py").read_text(encoding="utf-8")
        ws.write_file("canary_method.py", src)
        r = runner.run(rec.run_dir / "workspace", rec.run_dir / "scope" / "tests" / "public")
        assert r.all_passed and len(r.passed) == 7  # tested the sandbox's files, not the (stale) local directory
    assert holder["sandbox"].terminated
    assert holder["args"][2] == rec.caps.wall_clock_s
    exported = rec.run_dir / "workspace" / "canary_method.py"
    assert exported.exists() and "def ema" in exported.read_text(encoding="utf-8")
    assert not (rec.run_dir / "workspace" / ".assignment").exists()
    assert [e["action"] for e in log.events("sandbox")] == ["created", "seeded_from_export", "checkpoint", "exported", "terminated"]


def test_resume_seeds_sandbox_from_exported_workspace(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    log = BuildLog(rec.run_dir / "build.log")
    with _session(rec, tmp_path, log, holder) as (ws, runner):
        ws.write_file("canary_method.py", "# attempt one\n")
    shutil.rmtree(tmp_path / "sb")  # the old sandbox is gone; a new one must be seeded from the export
    with _session(rec, tmp_path, log, holder) as (ws, runner):
        assert ws.read_file("canary_method.py") == "# attempt one\n"


def test_sandbox_failure_is_an_error_outcome_and_exports_what_it_can(tmp_path, canary_dir, monkeypatch):
    import paper2code.sandbox.modal_session as ms

    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    monkeypatch.setattr(ms, "create_builder_sandbox", _factory(tmp_path, holder))
    monkeypatch.setattr(ms, "ModalWorkspace", _workspace_cls)
    monkeypatch.setattr(ms, "ModalTestRunner", _runner_cls)

    class DiesMidway:
        def build(self, ctx):
            ctx.workspace_api.write_file("canary_method.py", "# half done\n")
            raise ConnectionError("sandbox connection lost")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), DiesMidway())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "sandbox_failed" and "connection lost" in final.error.message
    assert (rec.run_dir / "workspace" / "canary_method.py").read_text(encoding="utf-8") == "# half done\n"
    assert holder["sandbox"].terminated
    rows = BuildLog(rec.run_dir / "build.log").read()
    assert rows[0]["event"] == "session_start" and rows[0]["sandbox"] is True
    assert rows[-1]["event"] == "session_end" and rows[-1]["reason"] == "sandbox_failed"


def test_sandbox_creation_failure_is_sandbox_failed(tmp_path, canary_dir, monkeypatch):
    import paper2code.sandbox.modal_session as ms

    rec = _seed_run(tmp_path, canary_dir)
    monkeypatch.setattr(ms, "create_builder_sandbox", _factory(tmp_path, {}, fail=True))

    class Never:
        def build(self, ctx):
            raise AssertionError("must not run")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), Never())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "sandbox_failed" and "no capacity" in final.error.message


def test_stub_builder_in_modal_mode_uses_remote_runner_without_a_sandbox(tmp_path, canary_dir, monkeypatch):
    """`--builder stub` without --no-gpu: the reference is copied locally, only the tests go remote."""
    import paper2code.sandbox.factory as factory
    import paper2code.sandbox.modal_session as ms
    from paper2code.agents.builder.stub import StubBuilder

    def no_sandbox(*args):
        raise AssertionError("the stub builder must not open a sandbox")

    rec = _seed_run(tmp_path, canary_dir)
    monkeypatch.setattr(ms, "create_builder_sandbox", no_sandbox)
    monkeypatch.setattr(factory, "make_runner", lambda ctx: _runner_cls(120, 10_000_000))
    ctx = RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), no_gpu=False, builder="stub",
                     reference_dir=canary_dir / "reference")
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, StubBuilder(canary_dir / "reference"))
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is None and final.counters.test_runs_used == 1 and final.budget.gpu_seconds > 0


def test_agent_builder_uses_workspace_api_when_given(tmp_path, canary_dir):
    from paper2code.agents.builder.agent import AgentBuilder
    from paper2code.manager.stages.build import BuildSession
    from paper2code.sandbox.runner import LocalTestRunner
    from paper2code.sandbox.workspace import LocalWorkspace
    from tests.test_agent_builder import ALLOWED, FakeClient, _factory as client_factory, _init, _result

    rec = _seed_run(tmp_path, canary_dir)
    log = BuildLog(rec.run_dir / "build.log")
    session = BuildSession(rec, LocalTestRunner(60), log)
    other = LocalWorkspace(tmp_path / "elsewhere")
    ctx = BuildContext(workspace=rec.run_dir / "workspace", spec_path=rec.run_dir / "scope" / "spec.md",
                       interface_path=rec.run_dir / "scope" / "interface.md", public_tests=rec.run_dir / "scope" / "tests" / "public",
                       run_tests=session.run_tests, give_up=session.give_up, session=session, workspace_api=other)
    holder = {}
    builder = AgentBuilder(Config(), client_factory=client_factory(FakeClient([[_init(ALLOWED), _result()]])))
    builder.on_tools_ready = lambda tools: holder.__setitem__("ws", tools.workspace)
    builder.build(ctx)
    assert holder["ws"] is other


def test_agent_startup_failure_in_modal_mode_is_agent_failed_not_sandbox_failed(tmp_path, canary_dir, monkeypatch):
    """The SDK failing to start the CLI is the manager's machine, not the sandbox; the record says which."""
    import paper2code.sandbox.modal_session as ms
    from paper2code.agents.builder.agent import AgentSessionError

    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    monkeypatch.setattr(ms, "create_builder_sandbox", _factory(tmp_path, holder))
    monkeypatch.setattr(ms, "ModalWorkspace", _workspace_cls)
    monkeypatch.setattr(ms, "ModalTestRunner", _runner_cls)

    class CannotStart:
        def build(self, ctx):
            raise AgentSessionError("Failed to start Claude Code")

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), CannotStart())
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "agent_failed" and "start Claude Code" in final.error.message
    assert holder["sandbox"].terminated


def _modal_mode(monkeypatch, tmp_path, holder, remote=execute_tests):
    import paper2code.sandbox.modal_session as ms

    def runner_cls(timeout_s, max_payload_bytes, snapshot_source=None, app_name="paper2code"):
        return ModalTestRunner(timeout_s=timeout_s, max_payload_bytes=max_payload_bytes, remote=remote, snapshot_source=snapshot_source)

    monkeypatch.setattr(ms, "create_builder_sandbox", _factory(tmp_path, holder))
    monkeypatch.setattr(ms, "ModalWorkspace", _workspace_cls)
    monkeypatch.setattr(ms, "ModalTestRunner", runner_cls)


class _ToolUser:
    """A builder that goes through BuilderTools exactly as the agent driver does."""

    def __init__(self, script):
        self.script = script  # callable(tools, holder)
        self.calls = []

    def build(self, ctx):
        from paper2code.agents.builder.tools import BuilderTools

        tools = BuilderTools(ctx.session, ctx.workspace_api, ctx.session.log, 60)
        self.script(tools, self.calls)


def test_dead_sandbox_during_a_tool_call_ends_the_run_as_sandbox_failed(tmp_path, canary_dir, monkeypatch):
    """Review focus 1 on the real path: the failure surfaces inside a tool call, not from build()."""
    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    _modal_mode(monkeypatch, tmp_path, holder)
    src = (canary_dir / "reference" / "canary_method.py").read_text(encoding="utf-8")

    def script(tools, calls):
        tools.call("write_file", {"path": "canary_method.py", "content": "def ema(xs, alpha):\n    raise NotImplementedError\n"})
        calls.append(tools.call("run_tests", {}))  # fails, and checkpoints the tested tree into the run dir
        tools.call("write_file", {"path": "canary_method.py", "content": src})  # the fix, never tested
        holder["sandbox"].dead = True
        calls.append(tools.call("bash", {"command": "ls"}))
        calls.append(tools.call("read_file", {"path": "canary_method.py"}))

    builder = _ToolUser(script)
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), builder)
    assert builder.calls[0][1] is False and "FAIL" in builder.calls[0][0]
    assert builder.calls[1][1] is True and "session is over" in builder.calls[1][0].lower()
    assert builder.calls[2][1] is True and "session is over" in builder.calls[2][0].lower()
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "sandbox_failed" and "sandbox gone" in final.error.message
    assert final.counters.test_runs_used == 1 and final.workspace_sha256 is None
    # The last tested tree survived even though the sandbox could not be exported or terminated.
    assert "NotImplementedError" in (rec.run_dir / "workspace" / "canary_method.py").read_text(encoding="utf-8")
    rows = BuildLog(rec.run_dir / "build.log").read()
    actions = [r["action"] for r in rows if r["event"] == "sandbox"]
    assert actions == ["created", "checkpoint", "export_failed", "terminate_failed"]
    assert rows[-1]["event"] == "session_end" and rows[-1]["reason"] == "sandbox_failed"


def test_remote_failure_during_run_tests_ends_the_run_as_sandbox_failed(tmp_path, canary_dir, monkeypatch):
    """A forgotten deploy or a crashed GPU function must not loop the agent until the wall clock."""
    rec = _seed_run(tmp_path, canary_dir)
    holder = {}

    def broken_remote(payload, timeout_s):
        raise RuntimeError("NotFoundError: Lookup failed for Function 'run_tests_remote'")

    _modal_mode(monkeypatch, tmp_path, holder, remote=broken_remote)

    def script(tools, calls):
        tools.call("write_file", {"path": "canary_method.py", "content": "x = 1\n"})
        calls.append(tools.call("run_tests", {}))
        calls.append(tools.call("run_tests", {}))

    builder = _ToolUser(script)
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), builder)
    assert builder.calls[0][1] is True and "NotFoundError" in builder.calls[0][0]
    assert builder.calls[1][1] is True and "session is over" in builder.calls[1][0].lower()
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "sandbox_failed" and "NotFoundError" in final.error.message
    assert final.counters.test_runs_used == 0
    assert holder["sandbox"].terminated


def test_passing_tree_is_checkpointed_so_a_failed_export_cannot_look_like_tampering(tmp_path, canary_dir, monkeypatch):
    """Public tests pass, then the sandbox vanishes before the final export: the inspector must see the passing tree."""
    from paper2code.manager.graph import run_stage

    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    _modal_mode(monkeypatch, tmp_path, holder)
    src = (canary_dir / "reference" / "canary_method.py").read_text(encoding="utf-8")

    class PassThenVanish:
        def build(self, ctx):
            from paper2code.agents.builder.tools import BuilderTools

            tools = BuilderTools(ctx.session, ctx.workspace_api, ctx.session.log, 60)
            tools.call("write_file", {"path": "canary_method.py", "content": src})
            text, err = tools.call("run_tests", {})
            assert "ALL PUBLIC TESTS PASS" in text
            holder["sandbox"].dead = True

    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), PassThenVanish())
    mid = RunRecord.load(rec.run_dir)
    assert mid.outcome is None and mid.workspace_sha256
    actions = [r["action"] for r in BuildLog(rec.run_dir / "build.log").events("sandbox")]
    assert "export_failed" in actions and "checkpoint" in actions
    from paper2code.manager.freeze import manifest_sha256

    mid.stage = "build"
    mid.scope_manifest_sha256 = manifest_sha256(rec.run_dir / "scope")  # init-run anchors this; _seed_run does not
    mid.save()
    final = run_stage("inspect", rec.run_dir, RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120), no_gpu=True, builder="agent"))
    assert final.outcome is not Outcome.TESTS_TAMPERED and final.outcome is Outcome.COMPLETED


def test_fresh_modal_session_prompt_is_not_marked_resumed(tmp_path, canary_dir, monkeypatch):
    from paper2code.agents.builder.agent import AgentBuilder
    from tests.test_agent_builder import ALLOWED, FakeClient, _factory as client_factory, _init, _result

    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    _modal_mode(monkeypatch, tmp_path, holder)
    client = FakeClient([[_init(ALLOWED), _result()]])
    builder = AgentBuilder(Config(run_tests_timeout_s=120), client_factory=client_factory(client))
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), _ctx(tmp_path), builder)
    assert client.queries and "resumed session" not in client.queries[0]
    assert ".assignment" not in client.queries[0].split("The workspace already contains")[-1][:200]


def test_oversize_workspace_is_a_refused_run_tests_result_in_the_session(tmp_path, canary_dir):
    import random

    rec = _seed_run(tmp_path, canary_dir)
    holder = {}
    log = BuildLog(rec.run_dir / "build.log")
    ctx = RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120, payload_max_mb=1), no_gpu=False, builder="agent")
    with modal_build_session(rec, ctx, log, sandbox_factory=_factory(tmp_path, holder), workspace_cls=_workspace_cls, runner_cls=_runner_cls) as (ws, runner):
        ws.write_file("data.bin", random.Random(0).randbytes(2_000_000).decode("latin-1"))
        r = runner.run(rec.run_dir / "workspace", rec.run_dir / "scope" / "tests" / "public")
        assert r.all_passed is False and r.returncode == -2 and "payload" in r.output
        assert holder["sandbox"].bytes_read == 0
