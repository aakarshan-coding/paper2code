"""The SDK driver with a fake client. The SDK's own message classes are used so the loop is
exercised against the real types; only the transport is faked."""
import asyncio
import dataclasses
from contextlib import asynccontextmanager

import pytest
from claude_agent_sdk import AssistantMessage, RateLimitEvent, RateLimitInfo, ResultMessage, SystemMessage, TextBlock

from paper2code.agents.builder.agent import AgentBuilder, AgentSessionError, RateLimited, build_options, find_cli
from paper2code.agents.builder.tools import ALLOWED, BuilderTools
from paper2code.config import Config
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.stages import build as build_stage
from paper2code.manager.stages.build import BuildSession
from paper2code.sandbox.runner import LocalTestRunner
from paper2code.sandbox.workspace import LocalWorkspace
from tests.test_build_stage import _ctx, _seed_run


def _mk(cls, **kw):
    """Construct an SDK dataclass with defaults for every field not given."""
    args = {}
    for f in dataclasses.fields(cls):
        if f.name in kw:
            args[f.name] = kw[f.name]
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:  # type: ignore[attr-defined]
            args[f.name] = None
    return cls(**args)


def _init(tools):
    return _mk(SystemMessage, subtype="init", data={"tools": tools})


def _result(**kw):
    base = dict(subtype="success", duration_ms=10, duration_api_ms=5, is_error=False, num_turns=1, session_id="s",
                total_cost_usd=0.01, usage={"input_tokens": 100, "output_tokens": 20})
    base.update(kw)
    return _mk(ResultMessage, **base)


class FakeClient:
    """Yields scripted messages per query. `on_query` may drive the tools directly to simulate the agent."""

    def __init__(self, script, on_query=None):
        self.script = list(script)  # list of message lists, one per query
        self.on_query = on_query
        self.queries = []

    async def query(self, prompt):
        self.queries.append(prompt)
        if self.on_query:
            self.on_query(len(self.queries), prompt)

    async def receive_response(self):
        for msg in (self.script.pop(0) if self.script else [_result()]):
            yield msg


def _factory(client):
    @asynccontextmanager
    async def make(options):
        yield client

    return make


def _wire(tmp_path, canary_dir):
    rec = _seed_run(tmp_path, canary_dir)
    ctx = _ctx(tmp_path, None)
    return rec, ctx


def test_find_cli_rejects_cmd_shims(monkeypatch, tmp_path):
    exe = tmp_path / "claude.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", str(exe))
    assert find_cli() == str(exe)
    shim = tmp_path / "claude.cmd"
    shim.write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", str(shim))
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert find_cli() is None


def test_build_options_lock_the_session_down(tmp_path, canary_dir):
    rec, ctx = _wire(tmp_path, canary_dir)
    log = BuildLog(rec.run_dir / "build.log")
    session = BuildSession(rec, LocalTestRunner(60), log)
    tools = BuilderTools(session, LocalWorkspace(rec.run_dir / "workspace"), log, 60)
    opts = build_options(tools, Config(), session, log, cwd=str(tmp_path))
    assert opts.tools == [] and opts.strict_mcp_config is True and opts.setting_sources == []
    assert opts.allowed_tools == ALLOWED and set(opts.mcp_servers) == {"paper2code"}
    assert opts.env["ANTHROPIC_API_KEY"] == "" and opts.env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert opts.permission_mode == "default" and opts.max_turns == 200
    assert "PreToolUse" in opts.hooks and "PreCompact" in opts.hooks
    deny = asyncio.run(opts.can_use_tool("Bash", {}, None))
    assert type(deny).__name__ == "PermissionResultDeny"
    allow = asyncio.run(opts.can_use_tool(ALLOWED[0], {}, None))
    assert type(allow).__name__ == "PermissionResultAllow"


def test_session_aborts_if_extra_tools_are_visible(tmp_path, canary_dir):
    rec, ctx = _wire(tmp_path, canary_dir)
    client = FakeClient([[_init(ALLOWED + ["mcp__gmail__send_message"]), _result()]])
    builder = AgentBuilder(Config(), client_factory=_factory(client))
    with pytest.raises(AgentSessionError, match="outside the allowlist"):
        build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, builder)
    assert not list((rec.run_dir / "workspace").iterdir())


def test_rate_limit_event_raises_rate_limited(tmp_path, canary_dir):
    rec, ctx = _wire(tmp_path, canary_dir)
    info = _mk(RateLimitInfo, status="rejected", rate_limit_type="five_hour")
    client = FakeClient([[_init(ALLOWED), _mk(RateLimitEvent, rate_limit_info=info, uuid="u", session_id="s")]])
    builder = AgentBuilder(Config(), client_factory=_factory(client))
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, builder)
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is Outcome.ERROR and final.error.reason == "rate_limited"


def test_scripted_agent_reaches_all_public_passed(tmp_path, canary_dir):
    """A fake client that behaves like an agent: writes the reference solution, then calls run_tests."""
    rec, ctx = _wire(tmp_path, canary_dir)
    holder = {}

    def on_query(n, prompt):
        tools = holder["tools"]
        if n == 1:
            assert "canary_method.py" in prompt and "spec.md" in prompt
            src = (canary_dir / "reference" / "canary_method.py").read_text(encoding="utf-8")
            tools.call("write_file", {"path": "canary_method.py", "content": src})
            text, err = tools.call("run_tests", {})
            assert "ALL PUBLIC TESTS PASS" in text

    client = FakeClient([[_init(ALLOWED), _mk(AssistantMessage, content=[TextBlock(text="done")], model="m"), _result()]], on_query=on_query)
    builder = AgentBuilder(Config(run_tests_timeout_s=120), client_factory=_factory(client))
    builder.on_tools_ready = lambda tools: holder.__setitem__("tools", tools)
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, builder)
    final = RunRecord.load(rec.run_dir)
    assert final.outcome is None and final.counters.test_runs_used == 1
    assert final.budget.spent_tokens == 120
    events = [e["event"] for e in BuildLog(rec.run_dir / "build.log").read()]
    assert events[0] == "session_start" and "tool_call" in events and "usage" in events and events[-1] == "session_end"
    assert len(client.queries) == 1  # no nudge after the session ended


def test_tools_are_refused_after_session_ends(tmp_path, canary_dir):
    rec, ctx = _wire(tmp_path, canary_dir)
    holder = {}

    def on_query(n, prompt):
        tools = holder["tools"]
        src = (canary_dir / "reference" / "canary_method.py").read_text(encoding="utf-8")
        tools.call("write_file", {"path": "canary_method.py", "content": src})
        tools.call("run_tests", {})
        text, err = tools.call("write_file", {"path": "canary_method.py", "content": "def ema(*a): return [0.1]\n"})
        assert err and "session is over" in text.lower()

    client = FakeClient([[_init(ALLOWED), _result()]], on_query=on_query)
    builder = AgentBuilder(Config(run_tests_timeout_s=120), client_factory=_factory(client))
    builder.on_tools_ready = lambda tools: holder.__setitem__("tools", tools)
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, builder)
    assert "def ema(xs" in (rec.run_dir / "workspace" / "canary_method.py").read_text(encoding="utf-8")
    tools = holder["tools"]
    hook = build_options(tools, Config(), tools.session, BuildLog(rec.run_dir / "build.log"), cwd=str(tmp_path)).hooks["PreToolUse"][0].hooks[0]
    out = asyncio.run(hook({"tool_name": ALLOWED[0], "tool_input": {}, "hook_event_name": "PreToolUse"}, "id", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_nudges_when_agent_stops_early_then_gives_up_on_cap(tmp_path, canary_dir):
    rec, ctx = _wire(tmp_path, canary_dir)
    client = FakeClient([[_init(ALLOWED), _result()], [_result()], [_result()], [_result()]])
    builder = AgentBuilder(Config(), client_factory=_factory(client))
    build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, builder)
    assert len(client.queries) == 4  # first prompt + 3 nudges
    assert RunRecord.load(rec.run_dir).outcome is Outcome.INCOMPLETE_STUCK  # builder_returned


def test_error_result_raises_session_error(tmp_path, canary_dir):
    rec, ctx = _wire(tmp_path, canary_dir)
    client = FakeClient([[_init(ALLOWED), _result(subtype="error_during_execution", is_error=True)]])
    builder = AgentBuilder(Config(), client_factory=_factory(client))
    with pytest.raises(AgentSessionError, match="error_during_execution"):
        build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, builder)
