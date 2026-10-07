# paper2code Step 4a: Agent SDK Builder (local workspace) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stub builder with a real Claude coding agent driven through the Claude Agent SDK under the author's Max subscription, confined to its workspace by construction, with the two manager-owned buttons (`run_tests`, `give_up`), the four caps from spec 9.3, rate-limit handling, and a complete build log, so that a scoped run reaches `completed` with an agent that never saw the hidden tests. Step 4b (a separate plan) moves the workspace and the test runner onto Modal; this plan keeps both local.

**Architecture:** The SDK session runs in the manager process; the agent gets **no built-in tools**. Every action it can take is one of six custom tools served from an in-process MCP server: `bash`, `read_file`, `write_file`, `list_files` (proxied into a `Workspace` object that confines paths to the workspace root), and the manager-owned `run_tests` and `give_up` (which call the existing `BuildSession`). The spec, interface, and public tests are inlined into the first prompt; they are never writable. A `PreToolUse` hook refuses every tool call once the session is over (all public tests passed, a cap was hit, or `give_up` was called), so the agent cannot keep editing after the manager has decided. The session's advertised tool list is checked at startup and the run aborts if anything beyond the six tools is visible. A `LocalWorkspace` implements `Workspace` with subprocesses; step 4b adds `ModalWorkspace` behind the same interface.

**Tech Stack:** `claude-agent-sdk` 0.2.164 (installed; `ClaudeSDKClient`, `ClaudeAgentOptions`, `tool`, `create_sdk_mcp_server`, `HookMatcher`, `RateLimitEvent`, `ResultMessage`), the `claude` native binary already on this machine (`CLAUDE_CODE_EXECPATH`), existing `BuildSession`, `LocalTestRunner`, `BuildLog`.

**Spec:** `docs/superpowers/specs/2026-09-30-paper2code-design.md` (sections 2.1, 4.1, 9, 13, 15, 16)

**Facts established before planning (2026-10-07):**
- `claude_agent_sdk` 0.2.164 exports `RateLimitEvent(rate_limit_info, uuid, session_id)` with `RateLimitInfo(status, resets_at, rate_limit_type, utilization, ...)`; `ClaudeSDKClient` has `interrupt()`, `query()`, `receive_response()`; `ClaudeAgentOptions` has `tools`, `allowed_tools`, `disallowed_tools`, `mcp_servers`, `strict_mcp_config`, `setting_sources`, `permission_mode`, `can_use_tool`, `hooks`, `max_turns`, `max_budget_usd`, `cwd`, `env`, `cli_path`, `model`, `effort`. `SystemMessage(subtype, data)`; the `init` message's `data["tools"]` lists the session's tools. `ResultMessage` has `usage`, `total_cost_usd`, `num_turns`, `duration_ms`, `is_error`, `subtype`, `stop_reason`.
- The sibling project `../designloop/src/designloop/agent/claude_sdk.py` runs this exact lockdown under the same account and found that without it the nested session exposed the account's mail, calendar, and drive connectors. Its pattern (`tools=[]`, `strict_mcp_config=True`, `setting_sources=[]`, `allowed_tools`, deny-by-default `can_use_tool`, init tool-list check, `env={"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}`, `cli_path` to a native binary, never a `.cmd` shim) is adopted wholesale.
- A native CLI exists at `$CLAUDE_CODE_EXECPATH` (the VS Code extension's `claude.exe`); `C:\Program Files\nodejs\claude` is a shim. The SDK merges `env` over the inherited environment.
- `@tool(name, description, input_schema: type | dict)` wraps `async def handler(args: dict) -> dict` returning `{"content": [{"type": "text", "text": ...}], "is_error": bool}`. Tools are referenced as `mcp__<server>__<tool>`.
- Hook callbacks are `async def hook(input_data: dict, tool_use_id: str | None, context) -> dict`; a `PreToolUse` hook denies with `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}`. `PreCompact` hooks receive `trigger` and `transcript_path`.
- Subscription auth: the CLI binary is already logged in on this machine; `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) is the unattended equivalent and is what step 4b/6 will use on Modal. `ANTHROPIC_API_KEY` must be blanked in the subprocess env.

## Global Constraints

- Builder harness is the Claude Agent SDK under the Max subscription (spec 2); `ANTHROPIC_API_KEY` must not be set in the builder's environment (spec 2.1).
- Builder inputs: the paper, `spec.md`, `interface.md`, the public tests (spec 9.2). Tools: shell inside the sandbox, read and write under `workspace/`, plus manager-owned `run_tests` and `give_up` (spec 9.2). In this plan the paper text is not inlined (it can be 80k characters); the spec and interface are the assignment. Deviation recorded.
- `run_tests` snapshots the workspace, runs the public suite out of the builder's reach, records GPU seconds and exact results to `build.log`, returns results to the builder; only `run_tests` results count; test history is written by the manager (spec 9.2).
- Caps with defaults (spec 9.3): `run_tests` calls 25 → `incomplete_budget`; wall clock 2 h (`caps.wall_clock_s` 7200) → `incomplete_budget`; GPU dollars (`budget.limit_usd`) → `incomplete_budget`; stall, identical failing set `caps.stall_n` (5) consecutive runs → `incomplete_stuck`. Builder tokens are recorded, not metered in dollars.
- When `run_tests` reports all public tests passing, the manager ends the session; the builder does not decide it is done (spec 9.3).
- Subscription rate limit mid-build: outcome `error`, reason `rate_limited`, workspace and build log preserved, no retry (spec 9.3).
- Compaction must preserve the current failing-test list and the last three attempts' summaries (spec 9.4): every `run_tests` result restates both, and a `PreCompact` hook logs a `compaction` event.
- Canary (spec 15): a simulated subscription rate-limit error during build must produce `error` with reason `rate_limited` and leave the workspace and build log intact.
- Resume rule: the build node re-runs after a crash; `build.log` is append-only and `test_runs_used` is persisted after every call, so a re-run continues the count rather than restarting it.
- Local mode caveat (spec 14): `LocalWorkspace.exec` runs a real shell on the manager's machine with a credential-scrubbed environment; path confinement applies to `read_file`/`write_file`/`list_files` only. Real confinement is step 4b. The README says so.
- Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; tests with `TEMP`/`TMP`/`TMPDIR` on the scratchpad; big edits via a Python patch script.

## Review Focus

1. **The agent keeps calling tools after the manager has ended the session** (all tests passed, cap hit, or gave up). Every further tool call must be refused by the hook, the refusal logged, and the workspace left as it was at the moment of the decision. Pinned to Task 4 (`test_tools_are_refused_after_session_ends`).
2. **A `write_file`/`read_file` path that escapes the workspace** (`../`, absolute, symlink-free traversal). Must be refused with an error result, nothing written. Pinned to Task 2 (`test_paths_outside_root_are_refused`).
3. **A subscription rate-limit event mid-session.** Outcome `error/rate_limited`, workspace and `build.log` intact, token usage so far recorded, no retry. Pinned to Task 5 (`test_rate_limit_canary_preserves_workspace_and_log`).
4. **A crash or kill mid-build then a re-run.** The re-run resumes the test-run count and stall history from `run.json`/`build.log` instead of granting a fresh 25 runs. Pinned to Task 1 (`test_session_resumes_counters_from_record_and_log`).
5. **The session advertises a tool outside the allowlist** (a connector leaked from the account). The run aborts before the first prompt with a clear error; nothing is built. Pinned to Task 4 (`test_session_aborts_if_extra_tools_are_visible`).

---

### Task 1: Caps and the build session

**Files:**
- Create: `src/paper2code/manager/caps.py`
- Modify: `src/paper2code/manager/stages/build.py`
- Modify: `src/paper2code/config.py`, `config.yaml`
- Test: `tests/test_caps.py`, `tests/test_build_stage.py`

**Interfaces:**
- Consumes: `RunRecord`, `Caps`, `BuildLog`, `TestRunResult`, `BuildFinished`, `Outcome` (steps 1 to 3).
- Produces:
  - `caps.py`: constants `TEST_RUNS_CAP = "test_runs_cap"`, `WALL_CLOCK_CAP = "wall_clock_cap"`, `GPU_BUDGET_CAP = "gpu_budget_cap"`, `STALL = "stall"`; `gpu_cost_usd(gpu_seconds, gpu_usd_per_hour) -> float`; `cap_before_run(record, elapsed_s, gpu_usd_per_hour) -> str | None` (test-runs cap, wall clock, GPU dollars, checked in that order); `stalled(failing_history: list[frozenset[str]], stall_n: int) -> bool` (last `stall_n` entries identical and non-empty); `outcome_for(finish_reason) -> Outcome | None`: `all_public_passed` → `None`; `test_runs_cap`, `wall_clock_cap`, `gpu_budget_cap` → `incomplete_budget`; `give_up`, `stall`, `builder_returned` → `incomplete_stuck`.
  - `BuildSession.__init__(record, runner, log, gpu_usd_per_hour: float = 1.0, now=time.monotonic)`; new attributes `started_at: float`, `failing_history: list[frozenset[str]]` (seeded from existing `run_tests` events in the log), `elapsed_s` property. `run_tests()` checks `cap_before_run` first (finishing with that reason and raising `BuildFinished`), then runs, records, then checks `stalled` (finishing with `STALL`). It returns a `TestRunResult`; the text summary for the agent is built by Task 3.
  - `BuildSession.check_wall_clock() -> bool`: if elapsed exceeds the cap and the session is not finished, finishes with `wall_clock_cap` and returns True (used by the hook in Task 4).
  - `run_with_builder` maps `session.finish_reason` through `outcome_for` and logs `{"event": "session_end", "reason": ..., "elapsed_s": ...}`.
  - Config: `builder_max_turns: int = 200`, `builder_tool_timeout_s: int = 300` (bash call timeout), `builder_model: str` via `models["builder"]` ("" = SDK default).

- [ ] **Step 1: Write the failing tests**

`tests/test_caps.py`:

```python
from datetime import date

import pytest

from paper2code.manager.caps import (
    GPU_BUDGET_CAP,
    STALL,
    TEST_RUNS_CAP,
    WALL_CLOCK_CAP,
    cap_before_run,
    gpu_cost_usd,
    outcome_for,
    stalled,
)
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, create_run


def _rec(tmp_path, **caps):
    return create_run(tmp_path, date(2026, 10, 7), Caps(**caps), limit_usd=1.0)


def test_gpu_cost():
    assert gpu_cost_usd(3600, 1.0) == pytest.approx(1.0)
    assert gpu_cost_usd(0, 5.0) == 0.0


def test_cap_before_run_order(tmp_path):
    rec = _rec(tmp_path, test_runs=2, wall_clock_s=100)
    assert cap_before_run(rec, elapsed_s=0, gpu_usd_per_hour=1.0) is None
    rec.counters.test_runs_used = 2
    assert cap_before_run(rec, 0, 1.0) == TEST_RUNS_CAP
    rec.counters.test_runs_used = 0
    assert cap_before_run(rec, 100, 1.0) == WALL_CLOCK_CAP
    rec.budget.gpu_seconds = 3600  # 1 USD at 1 USD/h against a 1 USD limit
    assert cap_before_run(rec, 0, 1.0) == GPU_BUDGET_CAP
    rec.budget.gpu_seconds = 3599
    assert cap_before_run(rec, 0, 1.0) is None


def test_stalled_requires_n_identical_nonempty_sets():
    a = frozenset({"t::x", "t::y"})
    assert stalled([a, a, a], 3)
    assert not stalled([a, a], 3)
    assert not stalled([a, frozenset({"t::x"}), a], 3)
    assert not stalled([frozenset(), frozenset(), frozenset()], 3)


def test_outcome_for_each_reason():
    assert outcome_for("all_public_passed") is None
    for r in (TEST_RUNS_CAP, WALL_CLOCK_CAP, GPU_BUDGET_CAP):
        assert outcome_for(r) is Outcome.INCOMPLETE_BUDGET
    for r in ("give_up", STALL, "builder_returned"):
        assert outcome_for(r) is Outcome.INCOMPLETE_STUCK
    with pytest.raises(KeyError):
        outcome_for("made_up")
```

Append to `tests/test_build_stage.py`:

```python
from paper2code.agents.builder.base import BuildFinished as _BF
from paper2code.manager.caps import STALL, TEST_RUNS_CAP, WALL_CLOCK_CAP
from paper2code.manager.stages.build import BuildSession
from paper2code.sandbox.runner import TestRunResult


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_caps.py tests/test_build_stage.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.manager.caps'`.

- [ ] **Step 3: Write `src/paper2code/manager/caps.py`**

```python
"""Cap enforcement for the build loop (spec 9.3). Pure functions; the session calls them."""
from __future__ import annotations

from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord

TEST_RUNS_CAP = "test_runs_cap"
WALL_CLOCK_CAP = "wall_clock_cap"
GPU_BUDGET_CAP = "gpu_budget_cap"
STALL = "stall"
ALL_PUBLIC_PASSED = "all_public_passed"
GIVE_UP = "give_up"
BUILDER_RETURNED = "builder_returned"

_OUTCOMES: dict[str, Outcome | None] = {
    ALL_PUBLIC_PASSED: None,
    TEST_RUNS_CAP: Outcome.INCOMPLETE_BUDGET,
    WALL_CLOCK_CAP: Outcome.INCOMPLETE_BUDGET,
    GPU_BUDGET_CAP: Outcome.INCOMPLETE_BUDGET,
    GIVE_UP: Outcome.INCOMPLETE_STUCK,
    STALL: Outcome.INCOMPLETE_STUCK,
    BUILDER_RETURNED: Outcome.INCOMPLETE_STUCK,
}


def gpu_cost_usd(gpu_seconds: float, gpu_usd_per_hour: float) -> float:
    return gpu_seconds * gpu_usd_per_hour / 3600.0


def cap_before_run(record: RunRecord, elapsed_s: float, gpu_usd_per_hour: float) -> str | None:
    """The cap that forbids another run_tests call now, or None."""
    if record.counters.test_runs_used >= record.caps.test_runs:
        return TEST_RUNS_CAP
    if elapsed_s >= record.caps.wall_clock_s:
        return WALL_CLOCK_CAP
    if gpu_cost_usd(record.budget.gpu_seconds, gpu_usd_per_hour) >= record.budget.limit_usd:
        return GPU_BUDGET_CAP
    return None


def stalled(failing_history: list[frozenset[str]], stall_n: int) -> bool:
    """The last stall_n runs failed on exactly the same non-empty set of tests."""
    if stall_n <= 0 or len(failing_history) < stall_n:
        return False
    tail = failing_history[-stall_n:]
    return bool(tail[0]) and all(s == tail[0] for s in tail)


def outcome_for(finish_reason: str) -> Outcome | None:
    return _OUTCOMES[finish_reason]
```

- [ ] **Step 4: Update `BuildSession` and `run_with_builder` in `src/paper2code/manager/stages/build.py`**

Replace the module body from the constants down with:

```python
from paper2code.manager.caps import (
    ALL_PUBLIC_PASSED,
    BUILDER_RETURNED,
    GIVE_UP,
    STALL,
    cap_before_run,
    outcome_for,
    stalled,
)
import time


class BuildSession:
    """Manager-owned. Counts test runs, enforces caps, writes build.log, decides when the session is over."""

    def __init__(self, record: RunRecord, runner: TestRunner, log: BuildLog, gpu_usd_per_hour: float = 1.0, now=time.monotonic) -> None:
        self.record = record
        self.runner = runner
        self.log = log
        self.gpu_usd_per_hour = gpu_usd_per_hour
        self.now = now
        self.started_at = now()
        self.workspace = record.run_dir / "workspace"
        self.public_tests = record.run_dir / "scope" / "tests" / "public"
        self.finished = False
        self.finish_reason: str | None = None
        self.last_result: TestRunResult | None = None
        # Resume: the stall window continues across a crash because build.log is append-only.
        self.failing_history: list[frozenset[str]] = [
            frozenset(e.get("failed", [])) for e in log.events("run_tests")
        ]

    @property
    def elapsed_s(self) -> float:
        return self.now() - self.started_at

    def run_tests(self) -> TestRunResult:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        cap = cap_before_run(self.record, self.elapsed_s, self.gpu_usd_per_hour)
        if cap is not None:
            self.finish(cap)
            raise BuildFinished(cap)
        result = self.runner.run(self.workspace, self.public_tests)
        self.last_result = result
        self.record.counters.test_runs_used += 1
        self.record.counters.attempts += 1
        self.record.budget.gpu_seconds += result.gpu_seconds
        if result.all_passed:
            self.record.workspace_sha256 = result.workspace_sha256
        self.record.save()
        self.log.append({
            "event": "run_tests",
            "call": self.record.counters.test_runs_used,
            "passed": list(result.passed),
            "failed": list(result.failed),
            "timed_out": result.timed_out,
            "duration_s": round(result.duration_s, 3),
            "gpu_seconds": result.gpu_seconds,
            "elapsed_s": round(self.elapsed_s, 1),
        })
        self.failing_history.append(result.failing_set)
        if result.all_passed:
            self.finish(ALL_PUBLIC_PASSED)
        elif stalled(self.failing_history, self.record.caps.stall_n):
            self.finish(STALL)
        return result

    def give_up(self, reason: str) -> None:
        if self.finished:
            raise BuildFinished(self.finish_reason)
        self.log.append({"event": "give_up", "reason": reason})
        self.finish(GIVE_UP)

    def check_wall_clock(self) -> bool:
        """Finish with the wall-clock cap if time is up. Called by the agent driver's hook."""
        if not self.finished and self.elapsed_s >= self.record.caps.wall_clock_s:
            self.finish("wall_clock_cap")
            return True
        return False

    def finish(self, reason: str) -> None:
        self.finished = True
        self.finish_reason = reason


def run_with_builder(record: RunRecord, ctx: RunContext, builder: Builder) -> None:
    from paper2code.sandbox.factory import make_runner

    run_dir = record.run_dir
    scope = run_dir / "scope"
    workspace = run_dir / "workspace"
    workspace.mkdir(exist_ok=True)
    log = BuildLog(run_dir / "build.log")
    session = BuildSession(record, make_runner(ctx), log, gpu_usd_per_hour=ctx.config.gpu_usd_per_hour)
    build_ctx = BuildContext(
        workspace=workspace,
        spec_path=scope / "spec.md",
        interface_path=scope / "interface.md",
        public_tests=scope / "tests" / "public",
        run_tests=session.run_tests,
        give_up=session.give_up,
        session=session,
    )
    log.append({"event": "session_start", "builder": ctx.builder, "test_runs_used": record.counters.test_runs_used})
    try:
        builder.build(build_ctx)
    except BuildFinished:
        pass
    except Exception as exc:
        log.append({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
    if not session.finished:
        session.finish(BUILDER_RETURNED)
    log.append({"event": "session_end", "reason": session.finish_reason, "elapsed_s": round(session.elapsed_s, 1)})
    outcome = outcome_for(session.finish_reason)
    if outcome is not None:
        record.outcome = outcome
    record.save()


def run(record: RunRecord, ctx: RunContext) -> None:
    from paper2code.agents.builder.factory import make_builder

    run_with_builder(record, ctx, make_builder(ctx))
```

Remove the old module-level `ALL_PUBLIC_PASSED`, `GIVE_UP`, `BUILDER_RETURNED` constants (they now come from `caps`). Add `session: Any = None` as a new last field of `BuildContext` in `src/paper2code/agents/builder/base.py` (the agent driver needs the session for the hook; the stub builder ignores it).

- [ ] **Step 5: Config additions**

In `src/paper2code/config.py` add to `Config`:

```python
    builder_max_turns: int = 200
    builder_tool_timeout_s: int = 300
```

and the matching `load_config` keywords `builder_max_turns=int(raw.get("builder_max_turns", defaults.builder_max_turns))`, `builder_tool_timeout_s=int(raw.get("builder_tool_timeout_s", defaults.builder_tool_timeout_s))`. Append to `config.yaml`:

```yaml
builder_max_turns: 200        # Agent SDK turn cap; the wall-clock cap is the real limit
builder_tool_timeout_s: 300   # one bash call inside the workspace
```

- [ ] **Step 6: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_caps.py tests/test_build_stage.py -q` then `pytest -q`
Expected: all pass. The step 1 test `test_builder_that_never_passes_is_incomplete_stuck` still passes (`builder_returned` → `incomplete_stuck`).

- [ ] **Step 7: Commit**

```bash
git add src/paper2code/manager/caps.py src/paper2code/manager/stages/build.py src/paper2code/agents/builder/base.py src/paper2code/config.py config.yaml tests/test_caps.py tests/test_build_stage.py
git commit -m "Build session enforces the four caps and resumes its counters after a crash

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The workspace abstraction

**Files:**
- Create: `src/paper2code/sandbox/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `scrubbed_environment()` from `sandbox/runner.py`.
- Produces:
  - `ExecResult` frozen dataclass: `returncode: int`, `stdout: str`, `stderr: str`, `timed_out: bool`, `duration_s: float`.
  - `WorkspaceError(Exception)` for refused paths.
  - `Workspace` Protocol: `exec(command: str, timeout_s: int) -> ExecResult`; `read_file(path: str) -> str`; `write_file(path: str, content: str) -> None`; `list_files(path: str = "") -> list[str]` (relative posix paths, files only, sorted, `__pycache__` skipped); `root: Path`.
  - `LocalWorkspace(root: Path)`: paths are resolved under `root`; anything that resolves outside raises `WorkspaceError`; `exec` runs through `bash -c` when a bash is on PATH, else `shell=True`, with `cwd=root`, a scrubbed environment, `stdin=DEVNULL`, output truncated to 20,000 characters each.

- [ ] **Step 1: Write the failing tests**

`tests/test_workspace.py`:

```python
import sys

import pytest

from paper2code.sandbox.workspace import ExecResult, LocalWorkspace, WorkspaceError


def test_write_read_list_roundtrip(tmp_path):
    ws = LocalWorkspace(tmp_path)
    ws.write_file("pkg/mod.py", "x = 1\n")
    ws.write_file("top.txt", "hi")
    assert ws.read_file("pkg/mod.py") == "x = 1\n"
    assert ws.list_files() == ["pkg/mod.py", "top.txt"]
    assert ws.list_files("pkg") == ["pkg/mod.py"]


def test_paths_outside_root_are_refused(tmp_path):
    ws = LocalWorkspace(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    for bad in ("../secret.txt", str(tmp_path / "secret.txt"), "a/../../secret.txt", "/etc/passwd", "C:/Windows/win.ini"):
        with pytest.raises(WorkspaceError):
            ws.read_file(bad)
        with pytest.raises(WorkspaceError):
            ws.write_file(bad, "x")
    assert (tmp_path / "secret.txt").read_text(encoding="utf-8") == "no"
    assert ws.list_files() == []


def test_read_missing_file_is_a_workspace_error(tmp_path):
    with pytest.raises(WorkspaceError, match="not found"):
        LocalWorkspace(tmp_path).read_file("nope.py")


def test_exec_runs_in_root_with_scrubbed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    ws = LocalWorkspace(tmp_path)
    ws.write_file("hello.py", "import os, pathlib\nprint(pathlib.Path.cwd().name)\nprint('KEY' if 'OPENAI_API_KEY' in os.environ else 'NOKEY')\n")
    r = ws.exec(f'"{sys.executable}" hello.py', timeout_s=60)
    assert isinstance(r, ExecResult)
    assert r.returncode == 0 and r.timed_out is False
    assert r.stdout.split() == [tmp_path.name, "NOKEY"]


def test_exec_timeout_and_nonzero_exit(tmp_path):
    ws = LocalWorkspace(tmp_path)
    r = ws.exec(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout_s=2)
    assert r.timed_out is True
    r = ws.exec(f'"{sys.executable}" -c "import sys; sys.exit(3)"', timeout_s=30)
    assert r.returncode == 3


def test_exec_output_is_truncated(tmp_path):
    ws = LocalWorkspace(tmp_path)
    r = ws.exec(f'"{sys.executable}" -c "print(\'x\' * 50000)"', timeout_s=30)
    assert len(r.stdout) <= 20_100 and "truncated" in r.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_workspace.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.sandbox.workspace'`.

- [ ] **Step 3: Write `src/paper2code/sandbox/workspace.py`**

```python
"""Where the builder's actions land. LocalWorkspace is a directory on this machine; step 4b adds a
Modal sandbox behind the same interface. Paths are confined to the root; the shell is not (local
mode only; the real confinement is the sandbox)."""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from paper2code.sandbox.runner import scrubbed_environment

MAX_OUTPUT_CHARS = 20_000


class WorkspaceError(Exception):
    """A refused path or a missing file; reported to the agent as a tool error."""


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float


class Workspace(Protocol):
    root: Path

    def exec(self, command: str, timeout_s: int) -> ExecResult: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def list_files(self, path: str = "") -> list[str]: ...


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text) - MAX_OUTPUT_CHARS} more characters]"


class LocalWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            raise WorkspaceError(f"absolute paths are not allowed: {path}")
        full = (self.root / candidate).resolve()
        if full != self.root and self.root not in full.parents:
            raise WorkspaceError(f"path escapes the workspace: {path}")
        return full

    def read_file(self, path: str) -> str:
        full = self._resolve(path)
        if not full.is_file():
            raise WorkspaceError(f"file not found: {path}")
        return full.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    def list_files(self, path: str = "") -> list[str]:
        base = self._resolve(path) if path else self.root
        if not base.exists():
            return []
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in base.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )

    def exec(self, command: str, timeout_s: int) -> ExecResult:
        bash = shutil.which("bash")
        args = [bash, "-c", command] if bash else command
        start = time.monotonic()
        try:
            proc = subprocess.run(
                args, shell=bash is None, cwd=self.root, env=scrubbed_environment(),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            return ExecResult(-1, _truncate(out), _truncate(err), True, time.monotonic() - start)
        return ExecResult(proc.returncode, _truncate(proc.stdout), _truncate(proc.stderr), False, time.monotonic() - start)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_workspace.py -q`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/sandbox/workspace.py tests/test_workspace.py
git commit -m "Add the Workspace interface and LocalWorkspace with path confinement

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Builder prompts and the tool layer

**Files:**
- Create: `src/paper2code/agents/builder/prompts.py`
- Create: `src/paper2code/agents/builder/tools.py`
- Test: `tests/test_builder_tools.py`

**Interfaces:**
- Consumes: `BuildSession` (Task 1), `Workspace` (Task 2), `BuildFinished`, `TestRunResult`, `UNTRUSTED_NOTE`/`BEGIN`/`END` from the scout prompts.
- Produces:
  - `prompts.SYSTEM_PROMPT: str`; `prompts.render_task(spec_md, interface_md, public_tests: dict[str, str], module: str, allowed_packages, caps_text) -> str` (the first user prompt: the assignment inlined between markers, the rules, the module name to create).
  - `tools.TOOL_SPECS: list[dict]` with `name`, `description`, `schema` for `bash`, `read_file`, `write_file`, `list_files`, `run_tests`, `give_up`; `tools.SERVER = "paper2code"`; `tools.ALLOWED = [f"mcp__{SERVER}__{name}" ...]`.
  - `tools.BuilderTools(session: BuildSession, workspace: Workspace, log: BuildLog, tool_timeout_s: int)` with `call(name: str, args: dict) -> tuple[str, bool]` returning `(text, is_error)`, logging every call as `{"event": "tool_call", "tool": name, "args": <summary>, "ok": bool, "duration_s": ...}`. `run_tests` returns `format_run_tests(result, session)`; after the session is finished every call returns `("The session is over: <reason>.", True)` without touching the workspace.
  - `tools.format_run_tests(result: TestRunResult, session: BuildSession) -> str`: passed/failed counts, the failing ids with their messages (truncated), the output tail, the attempt number out of the cap, and the last three attempts' failing-set sizes (spec 9.4). On all-pass it says the session is over.

- [ ] **Step 1: Write the failing tests**

`tests/test_builder_tools.py`:

```python
import sys

from paper2code.agents.builder.prompts import SYSTEM_PROMPT, render_task
from paper2code.agents.builder.tools import ALLOWED, SERVER, TOOL_SPECS, BuilderTools, format_run_tests
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import Caps, RunRecord
from paper2code.manager.stages.build import BuildSession
from paper2code.sandbox.runner import TestRunResult
from paper2code.sandbox.workspace import LocalWorkspace
from tests.test_build_stage import _ScriptedRunner, _fail, _seed_run


def _tools(tmp_path, canary_dir, results):
    rec = _seed_run(tmp_path, canary_dir)
    (rec.run_dir / "workspace").mkdir(exist_ok=True)
    log = BuildLog(rec.run_dir / "build.log")
    session = BuildSession(rec, _ScriptedRunner(results), log, now=lambda: 0.0)
    return rec, BuilderTools(session, LocalWorkspace(rec.run_dir / "workspace"), log, tool_timeout_s=60), log


def test_tool_specs_and_allowlist():
    assert [t["name"] for t in TOOL_SPECS] == ["bash", "read_file", "write_file", "list_files", "run_tests", "give_up"]
    assert ALLOWED == [f"mcp__{SERVER}__{n}" for n in ("bash", "read_file", "write_file", "list_files", "run_tests", "give_up")]
    assert all(t["schema"]["type"] == "object" and t["schema"].get("additionalProperties") is False for t in TOOL_SPECS)


def test_prompts_mention_the_rules():
    for needle in ("run_tests", "give_up", "hidden", "hardcod", "NotImplementedError", "untrusted"):
        assert needle in SYSTEM_PROMPT, needle
    text = render_task("SPEC", "IFACE", {"test_claim.py": "CLAIMTEST"}, "canary_method", ["numpy"], "25 test runs, 2.0 hours")
    for needle in ("SPEC", "IFACE", "CLAIMTEST", "canary_method.py", "numpy", "25 test runs", "BEGIN", "END"):
        assert needle in text, needle


def test_file_tools_roundtrip_and_refuse_escapes(tmp_path, canary_dir):
    rec, tools, log = _tools(tmp_path, canary_dir, [])
    text, err = tools.call("write_file", {"path": "canary_method.py", "content": "x = 1\n"})
    assert not err and "written" in text
    text, err = tools.call("read_file", {"path": "canary_method.py"})
    assert not err and text == "x = 1\n"
    text, err = tools.call("list_files", {})
    assert not err and "canary_method.py" in text
    text, err = tools.call("read_file", {"path": "../scope/tests/hidden/test_claim_hidden.py"})
    assert err and "escapes" in text
    events = log.events("tool_call")
    assert [e["tool"] for e in events] == ["write_file", "read_file", "list_files", "read_file"]
    assert events[-1]["ok"] is False


def test_bash_tool_runs_in_workspace(tmp_path, canary_dir):
    rec, tools, log = _tools(tmp_path, canary_dir, [])
    text, err = tools.call("bash", {"command": f'"{sys.executable}" -c "print(42)"'})
    assert not err and "42" in text and "exit 0" in text


def test_run_tests_tool_formats_result_and_ends_session_on_pass(tmp_path, canary_dir):
    passing = TestRunResult(("test_units::test_a",), (), 0, False, 0.3, 0.0, "", "d")
    rec, tools, log = _tools(tmp_path, canary_dir, [_fail("test_claim::test_x[0]"), passing])
    text, err = tools.call("run_tests", {})
    assert not err and "1 failed" in text and "test_claim::test_x[0]" in text and "attempt 1 of 25" in text
    text, err = tools.call("run_tests", {})
    assert not err and "ALL PUBLIC TESTS PASS" in text and "session is over" in text.lower()
    assert tools.session.finished and tools.session.finish_reason == "all_public_passed"
    text, err = tools.call("bash", {"command": "echo still here"})
    assert err and "session is over" in text.lower()
    text, err = tools.call("write_file", {"path": "late.py", "content": "x"})
    assert err and not (rec.run_dir / "workspace" / "late.py").exists()


def test_give_up_tool(tmp_path, canary_dir):
    rec, tools, log = _tools(tmp_path, canary_dir, [])
    text, err = tools.call("give_up", {"reason": "dataset unreachable"})
    assert not err and tools.session.finish_reason == "give_up"
    assert log.events("give_up")[0]["reason"] == "dataset unreachable"


def test_format_run_tests_restates_recent_attempts(tmp_path, canary_dir):
    rec, tools, log = _tools(tmp_path, canary_dir, [_fail("a::1", "a::2"), _fail("a::1"), _fail("a::1"), _fail("a::1")])
    for _ in range(4):
        tools.call("run_tests", {})
    text = format_run_tests(tools.session.last_result, tools.session)
    assert "attempt 4 of 25" in text and "previous attempts" in text.lower()
    assert "a::1" in text


def test_unknown_tool_is_an_error(tmp_path, canary_dir):
    rec, tools, log = _tools(tmp_path, canary_dir, [])
    text, err = tools.call("delete_everything", {})
    assert err and "unknown tool" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_builder_tools.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.agents.builder.prompts'`.

- [ ] **Step 3: Write `src/paper2code/agents/builder/prompts.py`**

```python
"""What the builder is told. The assignment (spec, interface, public tests) is inlined between
markers; the rules are structural facts about the loop, not requests."""
from __future__ import annotations

from paper2code.agents.scout.prompts import BEGIN, END, UNTRUSTED_NOTE, _defang

SYSTEM_PROMPT = """You are the builder in an automated research-reproduction loop. You implement a small machine
learning method from a written assignment so that a pytest suite passes. You work alone in a
workspace directory through six tools and nothing else:

- bash(command): run a shell command in the workspace (CPU only; packages listed as allowed may
  be imported; pip install is permitted for them).
- read_file(path), write_file(path, content), list_files(path): the workspace only.
- run_tests(): the manager runs the PUBLIC test suite against a snapshot of your workspace and
  returns the results. Only these results count. Running pytest yourself is fine for iteration
  but proves nothing.
- give_up(reason): end the session honestly when you cannot make progress.

Facts about the loop:
- The tests you see are the public tests. HIDDEN tests exist that you cannot see: different seeds,
  a different data slice, perturbed hyperparameters, and a recomputation of the claim from your own
  primitives. An inspector will also read your code against the paper's method. Hardcoding
  results, detecting that you are under test, weakening the baseline, leaking evaluation data into
  training, or implementing a different method than the one described all end as a failed run.
  Implement the method as written, honestly.
- The session ends the moment run_tests reports every public test passing. You do not decide when
  you are done; the manager does. After that, tool calls are refused.
- There are caps: a fixed number of run_tests calls, a wall clock, and a GPU budget. Each
  run_tests result tells you where you stand. Spend them well: read the spec and interface first,
  write the module, iterate.
- The module to create is named in the task; the tests import from it. Every function and class in
  the interface must exist with the exact signature given. Stubs raise NotImplementedError; replace
  them with real implementations.

""" + UNTRUSTED_NOTE.replace("paper", "assignment")


def render_task(spec_md: str, interface_md: str, public_tests: dict[str, str], module: str, allowed_packages: list[str], caps_text: str) -> str:
    tests = "\n\n".join(f"### tests/public/{name}\n```python\n{_defang(body)}\n```" for name, body in sorted(public_tests.items()))
    return (
        f"Create the module `{module}.py` at the workspace root so that the public tests pass.\n"
        f"Allowed packages: {', '.join(allowed_packages)}. Caps: {caps_text}.\n\n"
        f"{BEGIN}\n## spec.md\n{_defang(spec_md)}\n\n## interface.md\n{_defang(interface_md)}\n\n## public tests\n{tests}\n{END}\n\n"
        "Start by reading the spec and interface above, then write the module, then call run_tests."
    )
```

- [ ] **Step 4: Write `src/paper2code/agents/builder/tools.py`**

```python
"""The builder's six tools as plain Python, unit-testable without the SDK. The SDK wrapper in
agent.py only marshals arguments and text."""
from __future__ import annotations

import time
from typing import Any

from paper2code.agents.builder.base import BuildFinished
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.stages.build import BuildSession
from paper2code.sandbox.runner import TestRunResult
from paper2code.sandbox.workspace import Workspace, WorkspaceError

SERVER = "paper2code"

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "bash",
        "description": "Run a shell command in the workspace directory (CPU only). Returns exit code, stdout and stderr, truncated.",
        "schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
    },
    {
        "name": "read_file",
        "description": "Read a text file from the workspace. Paths are relative to the workspace root.",
        "schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    },
    {
        "name": "write_file",
        "description": "Write (create or overwrite) a text file in the workspace. Paths are relative to the workspace root.",
        "schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False},
    },
    {
        "name": "list_files",
        "description": "List files in the workspace (optionally under a subdirectory).",
        "schema": {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "run_tests",
        "description": "Have the manager run the public test suite against a snapshot of the workspace. Only these results count. Counts against the run cap.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "give_up",
        "description": "End the session because you cannot make progress. Give a one-sentence reason.",
        "schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"], "additionalProperties": False},
    },
]
ALLOWED = [f"mcp__{SERVER}__{t['name']}" for t in TOOL_SPECS]
_MAX_MSG = 300


def format_run_tests(result: TestRunResult, session: BuildSession) -> str:
    used = session.record.counters.test_runs_used
    cap = session.record.caps.test_runs
    lines = [f"run_tests attempt {used} of {cap}: {len(result.passed)} passed, {len(result.failed)} failed"]
    if result.timed_out:
        lines.append("The run TIMED OUT. Make the experiment cheaper.")
    if result.all_passed:
        lines.append("ALL PUBLIC TESTS PASS. The session is over; the manager takes it from here.")
        return "\n".join(lines)
    for test_id in result.failed:
        msg = result.messages.get(test_id, "")[:_MAX_MSG]
        lines.append(f"- FAIL {test_id}: {msg}" if msg else f"- FAIL {test_id}")
    history = session.failing_history[:-1][-3:]
    if history:
        lines.append("Previous attempts (failing tests): " + ", ".join(str(len(h)) for h in history) + f", now {len(result.failed)}.")
    tail = result.output.strip().splitlines()[-40:]
    if tail:
        lines.append("Output tail:\n" + "\n".join(tail))
    if session.finished:
        lines.append(f"The session is over: {session.finish_reason}.")
    return "\n".join(lines)


class BuilderTools:
    def __init__(self, session: BuildSession, workspace: Workspace, log: BuildLog, tool_timeout_s: int) -> None:
        self.session = session
        self.workspace = workspace
        self.log = log
        self.tool_timeout_s = tool_timeout_s

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """(text for the agent, is_error). Every call is logged; nothing runs after the session ends."""
        start = time.monotonic()
        if self.session.finished:
            text, err = f"The session is over: {self.session.finish_reason}. No further actions are possible.", True
        else:
            try:
                text, err = self._dispatch(name, args)
            except BuildFinished as exc:
                text, err = f"The session is over: {exc}.", True
            except WorkspaceError as exc:
                text, err = str(exc), True
        self.log.append({
            "event": "tool_call", "tool": name, "args": _summarise(args), "ok": not err,
            "duration_s": round(time.monotonic() - start, 3),
        })
        return text, err

    def _dispatch(self, name: str, args: dict) -> tuple[str, bool]:
        if name == "bash":
            r = self.workspace.exec(str(args.get("command", "")), self.tool_timeout_s)
            status = "timed out" if r.timed_out else f"exit {r.returncode}"
            return f"[{status}, {r.duration_s:.1f}s]\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}", r.timed_out
        if name == "read_file":
            return self.workspace.read_file(str(args.get("path", ""))), False
        if name == "write_file":
            path = str(args.get("path", ""))
            content = str(args.get("content", ""))
            self.workspace.write_file(path, content)
            return f"written {path} ({len(content)} characters)", False
        if name == "list_files":
            files = self.workspace.list_files(str(args.get("path", "")))
            return "\n".join(files) if files else "(no files)", False
        if name == "run_tests":
            result = self.session.run_tests()
            return format_run_tests(result, self.session), False
        if name == "give_up":
            self.session.give_up(str(args.get("reason", "")))
            return "Session ended: gave up.", False
        return f"unknown tool {name!r}", True


def _summarise(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        s = str(v)
        out[k] = s if len(s) <= 120 else s[:120] + f"... [{len(s)} chars]"
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_builder_tools.py -q`
Expected: 8 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/paper2code/agents/builder/prompts.py src/paper2code/agents/builder/tools.py tests/test_builder_tools.py
git commit -m "Builder prompts and the six-tool layer over the session and workspace

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The Agent SDK driver

**Files:**
- Create: `src/paper2code/agents/builder/agent.py`
- Test: `tests/test_agent_builder.py`

**Interfaces:**
- Consumes: `BuilderTools`, `TOOL_SPECS`, `SERVER`, `ALLOWED` (Task 3), `BuildContext` with `session` (Task 1), `LocalWorkspace` (Task 2), `Config`.
- Produces:
  - `RateLimited(Exception)`, `AgentSessionError(Exception)`.
  - `find_cli() -> str | None`: `CLAUDE_CODE_EXECPATH`, then `shutil.which("claude")`, rejecting `.cmd`/`.bat`.
  - `AgentBuilder(config: Config, client_factory=None, workspace_factory=LocalWorkspace)` implementing `Builder.build(ctx)`. `client_factory(options) -> async context manager yielding an object with `query(str)` and `receive_response()`; default builds `ClaudeSDKClient(options=...)`.
  - `build_options(tools: BuilderTools, config, session, log, cwd) -> ClaudeAgentOptions` with the lockdown: `tools=[]`, `mcp_servers={SERVER: server}`, `strict_mcp_config=True`, `setting_sources=[]`, `allowed_tools=ALLOWED`, `can_use_tool` deny-by-default, `permission_mode="default"`, `max_turns=config.builder_max_turns`, `env={"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""}`, `cli_path=find_cli()`, `model=config.models["builder"] or None`, `system_prompt=SYSTEM_PROMPT`, hooks: `PreToolUse` (deny when `session.finished` or `session.check_wall_clock()`; logs `tool_refused`), `PreCompact` (logs `{"event": "compaction", "trigger": ..., "failing": [...]}`).
  - `drive(ctx, tools, options, client_factory, log) -> dict` (async): the message loop. On `SystemMessage` `init`: `extra = [t for t in data["tools"] if t not in ALLOWED]`; if `extra`, raise `AgentSessionError` before anything else. `RateLimitEvent` with `status == "rejected"` → raise `RateLimited`. `AssistantMessage`: `TextBlock` → log `assistant_text` (first 1,500 chars); `ToolUseBlock` → nothing (the tool layer logs the call itself). `ResultMessage`: record `usage`, `total_cost_usd`, `num_turns`, `duration_ms` into a `usage` dict and log `{"event": "usage", ...}`; if `is_error` and not finished, raise `AgentSessionError(subtype)`. After `receive_response` ends: if the session is not finished and turns remain, send one nudge ("Continue. Call run_tests when ready, or give_up.") at most `max_nudges=3` times; otherwise return.
  - `AgentBuilder.build(ctx)`: wires `LocalWorkspace(ctx.workspace)`, `BuilderTools`, options, runs `asyncio.run(drive(...))`, adds `usage["input_tokens"] + ["output_tokens"]` to `ctx.session.record.budget.spent_tokens`, re-raises `RateLimited`.

- [ ] **Step 1: Write the failing tests**

`tests/test_agent_builder.py`:

```python
"""The SDK driver with a fake client. The SDK's own message classes are used so the loop is
exercised against the real types; only the transport is faked."""
import asyncio
import dataclasses
from contextlib import asynccontextmanager

import pytest
from claude_agent_sdk import AssistantMessage, RateLimitEvent, RateLimitInfo, ResultMessage, SystemMessage, TextBlock, ToolUseBlock

from paper2code.agents.builder.agent import AgentBuilder, AgentSessionError, RateLimited, build_options, find_cli
from paper2code.agents.builder.base import BuildContext
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
    fields = {f.name: f for f in dataclasses.fields(cls)}
    args = {}
    for name, f in fields.items():
        if name in kw:
            args[name] = kw[name]
        elif f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:  # type: ignore[attr-defined]
            continue
        else:
            args[name] = None
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
    with pytest.raises(RateLimited):
        build_stage.run_with_builder(RunRecord.load(rec.run_dir), ctx, builder)


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
    hook = build_options(holder["tools"], Config(), holder["tools"].session, BuildLog(rec.run_dir / "build.log"), cwd=str(tmp_path)).hooks["PreToolUse"][0].hooks[0]
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_agent_builder.py -q`
Expected: `ModuleNotFoundError: No module named 'paper2code.agents.builder.agent'`. If constructing an SDK message in `_mk` fails on a required field, read `dataclasses.fields(<class>)` and pass that field; the SDK's dataclasses are the contract.

- [ ] **Step 3: Write `src/paper2code/agents/builder/agent.py`**

```python
"""The Agent SDK builder. Locked down: no built-in tools, strict MCP config, no user settings,
an allowlist of six tools, a deny-by-default permission callback, and a startup check of the
session's advertised tool list. The subscription token is used; any API key is blanked."""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from paper2code.agents.builder.base import BuildContext
from paper2code.agents.builder.prompts import SYSTEM_PROMPT, render_task
from paper2code.agents.builder.tools import ALLOWED, SERVER, TOOL_SPECS, BuilderTools
from paper2code.config import Config
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.stages.build import BuildSession
from paper2code.sandbox.workspace import LocalWorkspace

warnings.filterwarnings("ignore", message="can_use_tool will not be invoked")

NUDGE = "Continue. Call run_tests when you want the public suite run, or give_up if you cannot make progress."
MAX_NUDGES = 3


class AgentSessionError(RuntimeError):
    pass


class RateLimited(AgentSessionError):
    pass


def find_cli() -> str | None:
    """A native claude executable. The SDK refuses Windows .cmd shims."""
    for cand in (os.environ.get("CLAUDE_CODE_EXECPATH"),):
        if cand and Path(cand).exists() and not cand.lower().endswith((".cmd", ".bat")):
            return cand
    found = shutil.which("claude")
    if found and not found.lower().endswith((".cmd", ".bat")):
        return found
    return None


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": is_error}


def build_server(tools: BuilderTools):
    from claude_agent_sdk import create_sdk_mcp_server, tool

    sdk_tools = []
    for spec in TOOL_SPECS:
        def make(n: str, description: str, schema: dict):
            @tool(n, description, schema)
            async def handler(args, _n=n):
                text, err = tools.call(_n, dict(args or {}))
                return _text(text, err)

            return handler

        sdk_tools.append(make(spec["name"], spec["description"], spec["schema"]))
    return create_sdk_mcp_server(name=SERVER, version="1.0.0", tools=sdk_tools)


def build_options(tools: BuilderTools, config: Config, session: BuildSession, log: BuildLog, cwd: str):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, PermissionResultAllow, PermissionResultDeny

    async def can_use_tool(name, _input, _ctx):
        if name in ALLOWED:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Only the builder's six tools are available.", interrupt=False)

    async def refuse_after_end(input_data, tool_use_id, _context):
        session.check_wall_clock()
        if session.finished:
            log.append({"event": "tool_refused", "tool": input_data.get("tool_name"), "reason": session.finish_reason})
            return {"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": f"The session is over: {session.finish_reason}.",
            }}
        return {}

    async def on_compact(input_data, tool_use_id, _context):
        failing = sorted(session.failing_history[-1]) if session.failing_history else []
        log.append({"event": "compaction", "trigger": input_data.get("trigger"), "failing": failing,
                    "attempts": len(session.failing_history)})
        return {}

    model = config.models.get("builder") or None
    return ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={SERVER: build_server(tools)},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=ALLOWED,
        can_use_tool=can_use_tool,
        permission_mode="default",
        setting_sources=[],
        max_turns=config.builder_max_turns,
        cwd=cwd,
        env={"ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": ""},
        cli_path=find_cli(),
        hooks={
            "PreToolUse": [HookMatcher(hooks=[refuse_after_end])],
            "PreCompact": [HookMatcher(hooks=[on_compact])],
        },
    )


def _default_client_factory():
    from claude_agent_sdk import ClaudeSDKClient

    @asynccontextmanager
    async def make(options):
        async with ClaudeSDKClient(options=options) as client:
            yield client

    return make


async def drive(prompt: str, session: BuildSession, options, client_factory, log: BuildLog) -> dict:
    from claude_agent_sdk import AssistantMessage, RateLimitEvent, ResultMessage, SystemMessage, TextBlock

    usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "total_cost_usd": None, "num_turns": 0}
    nudges = 0
    async with client_factory(options) as client:
        await client.query(prompt)
        while True:
            async for msg in client.receive_response():
                if isinstance(msg, SystemMessage) and msg.subtype == "init":
                    visible = list((msg.data or {}).get("tools", []))
                    extra = [t for t in visible if t not in ALLOWED]
                    if extra:
                        raise AgentSessionError(f"session exposes tools outside the allowlist: {extra[:5]}")
                    log.append({"event": "session_init", "tools": visible})
                elif isinstance(msg, RateLimitEvent):
                    info = msg.rate_limit_info
                    if getattr(info, "status", "allowed") == "rejected":
                        log.append({"event": "rate_limited", "type": getattr(info, "rate_limit_type", None)})
                        raise RateLimited("subscription rate limit reached")
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content or []:
                        if isinstance(block, TextBlock) and block.text.strip():
                            log.append({"event": "assistant_text", "text": block.text.strip()[:1500]})
                elif isinstance(msg, ResultMessage):
                    u = msg.usage or {}
                    usage["input_tokens"] += int(u.get("input_tokens") or 0)
                    usage["output_tokens"] += int(u.get("output_tokens") or 0)
                    usage["total_cost_usd"] = msg.total_cost_usd
                    usage["num_turns"] += int(msg.num_turns or 0)
                    log.append({"event": "usage", "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                                "total_cost_usd": msg.total_cost_usd, "num_turns": msg.num_turns, "duration_ms": msg.duration_ms,
                                "subtype": msg.subtype})
                    if msg.is_error and not session.finished:
                        raise AgentSessionError(f"agent session ended with an error: {msg.subtype}")
            if session.finished or nudges >= MAX_NUDGES:
                break
            nudges += 1
            await client.query(NUDGE)
    usage["nudges"] = nudges
    return usage


class AgentBuilder:
    def __init__(self, config: Config, client_factory: Callable | None = None, workspace_factory=LocalWorkspace) -> None:
        self.config = config
        self.client_factory = client_factory or _default_client_factory()
        self.workspace_factory = workspace_factory
        self.on_tools_ready: Callable[[BuilderTools], None] | None = None  # test hook

    def build(self, ctx: BuildContext) -> None:
        session: BuildSession = ctx.session
        log = session.log
        workspace = self.workspace_factory(ctx.workspace)
        tools = BuilderTools(session, workspace, log, self.config.builder_tool_timeout_s)
        if self.on_tools_ready:
            self.on_tools_ready(tools)
        public_tests = {p.name: p.read_text(encoding="utf-8") for p in sorted(ctx.public_tests.glob("test_*.py"))}
        interface_md = ctx.interface_path.read_text(encoding="utf-8")
        module = _module_name(interface_md)
        caps = session.record.caps
        caps_text = f"{caps.test_runs} test runs, {caps.wall_clock_s / 3600:.1f} hours, {session.record.budget.limit_usd:.2f} USD of GPU"
        prompt = render_task(ctx.spec_path.read_text(encoding="utf-8"), interface_md, public_tests, module, self.config.allowed_packages, caps_text)
        scratch = tempfile.mkdtemp(prefix="p2c-agent-")
        try:
            options = build_options(tools, self.config, session, log, cwd=scratch)
            usage = asyncio.run(drive(prompt, session, options, self.client_factory, log))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        session.record.budget.spent_tokens += usage["input_tokens"] + usage["output_tokens"]
        session.record.save()


def _module_name(interface_md: str) -> str:
    import re

    m = re.search(r"Module `(\w+)`", interface_md)
    return m.group(1) if m else "solution"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_agent_builder.py -q`
Expected: 8 PASS. The `_mk` helper and the fake transport are the only test doubles; the SDK option and message classes are real.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/agents/builder/agent.py tests/test_agent_builder.py
git commit -m "Agent SDK builder: locked-down session, six tools, caps hook, rate-limit and usage handling

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Wiring, rate-limit canary, CLI default

**Files:**
- Modify: `src/paper2code/agents/builder/factory.py`
- Modify: `src/paper2code/manager/stages/build.py` (`run_with_builder` catches `RateLimited`)
- Modify: `src/paper2code/cli.py` (`--builder` default stays `stub` for `run`; `--reference` required only for `stub`)
- Test: `tests/test_build_stage.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `AgentBuilder`, `RateLimited` (Task 4).
- Produces: `make_builder(ctx)` returns `AgentBuilder(ctx.config)` for `"agent"`. `run_with_builder` catches `RateLimited`: logs `{"event": "session_end", "reason": "rate_limited"}`, sets `record.outcome = Outcome.ERROR`, `record.error = RunError("build", "rate_limited", str(exc))`, saves, does not raise (the workspace and log are left as they stood). CLI: `paper2code run --run DIR --no-gpu --builder agent` needs no `--reference`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_build_stage.py`:

```python
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
```

Append to `tests/test_cli.py`:

```python
def test_agent_builder_needs_no_reference_flag(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    # Parsing succeeds (no SystemExit); the run then fails fast because no fake client is wired, which is fine here.
    rc = main(["build", "--run", str(run_dir), "--no-gpu", "--builder", "agent", "--config", str(tmp_path / "absent.yaml")])
    assert rc in (0, 1)
    assert "requires --reference" not in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_build_stage.py tests/test_cli.py -q`
Expected: the canary test fails because `RateLimited` propagates; `make_builder` raises `NotImplementedError`; the CLI test exits 2 on `--reference`.

- [ ] **Step 3: Implement**

`src/paper2code/agents/builder/factory.py`:

```python
from __future__ import annotations

from paper2code.agents.builder.base import Builder
from paper2code.agents.builder.stub import StubBuilder
from paper2code.manager.graph import RunContext


def make_builder(ctx: RunContext) -> Builder:
    if ctx.builder == "stub":
        if ctx.reference_dir is None:
            raise ValueError("builder 'stub' needs reference_dir (CLI: --reference DIR)")
        return StubBuilder(ctx.reference_dir)
    if ctx.builder == "agent":
        from paper2code.agents.builder.agent import AgentBuilder

        return AgentBuilder(ctx.config)
    raise ValueError(f"unknown builder {ctx.builder!r}; expected 'stub' or 'agent'")
```

In `run_with_builder` (build.py) replace the `try` block with:

```python
    try:
        builder.build(build_ctx)
    except BuildFinished:
        pass
    except RateLimited as exc:
        # Infrastructure outcome, not the builder's failure: keep workspace and log as they stand, no retry today.
        log.append({"event": "session_end", "reason": "rate_limited", "elapsed_s": round(session.elapsed_s, 1)})
        record.outcome = Outcome.ERROR
        record.error = RunError(stage="build", reason="rate_limited", message=str(exc))
        record.save()
        return
    except Exception as exc:
        log.append({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        raise
```

with `from paper2code.agents.builder.agent import RateLimited` and `from paper2code.manager.record import RunError` added to the imports (the agent module imports `build.py` for `BuildSession`, so import `RateLimited` lazily inside `run_with_builder` to avoid a cycle: `from paper2code.agents.builder.agent import RateLimited` as the first line of the function).

In `cli.py` `_context`, the `--reference` check already applies only to `args.builder == "stub"`; confirm and leave. In `_reaches_build` nothing changes.

- [ ] **Step 4: Run the tests to verify they pass, then the whole suite**

Run: `pytest tests/test_build_stage.py tests/test_cli.py tests/test_agent_builder.py -q` then `pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/paper2code/agents/builder/factory.py src/paper2code/manager/stages/build.py src/paper2code/cli.py tests/test_build_stage.py tests/test_cli.py
git commit -m "Wire the agent builder; rate limit ends the run as error/rate_limited with workspace and log intact

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Live build on the canary under the subscription, docs, journal

**Files:**
- Create: `tests/test_live_agent.py`
- Modify: `README.md`, `decisions.md`

**Interfaces:** consumes everything above through the CLI.

- [ ] **Step 1: Write the opt-in live test**

`tests/test_live_agent.py`:

```python
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
    print("outcome:", rec.outcome, "| test runs:", rec.counters.test_runs_used, "| tokens:", rec.budget.spent_tokens)
```

- [ ] **Step 2: Run the live build**

Run: `PAPER2CODE_LIVE_BUILD=1 pytest tests/test_live_agent.py -q -s`
Expected: the agent writes `canary_method.py`, calls `run_tests` once or twice, the session ends on the first all-pass, inspect runs the hidden tests, outcome `completed`. Record in the ledger: outcome, number of `run_tests` calls, tokens, wall time, and the first assistant text line. If the SDK cannot authenticate, the error names the token: the author must run `claude setup-token` and set `CLAUDE_CODE_OAUTH_TOKEN`; record that as blocked and continue with Step 3 (the offline tests already prove the plumbing). If the session exposes extra tools, the startup check raises: record the list, do not weaken the check.

Also run the same by hand to see the summary:

```bash
paper2code init-run --scope tests/fixtures/canary/scope --paper-id canary-0001 --title "EMA denoising canary" --runs-root runs-live
paper2code run --run runs-live/$(date +%F) --no-gpu --builder agent
cat runs-live/$(date +%F)/summary.md
```

Move the run directory to the scratchpad afterwards.

- [ ] **Step 3: README and journal**

README Status paragraph becomes:

```markdown
Build step 4a of 6: a real Claude coding agent (Agent SDK, Max subscription) builds a scoped
assignment in a local workspace through six confined tools, with the four caps and rate-limit
handling. Step 4b moves the workspace and the GPU test runner onto Modal. The stub builder
remains for offline tests.
```

Add to the Local mode block:

```bash
paper2code run --run runs/<date> --no-gpu --builder agent        # real agent, local workspace (no sandbox)
PAPER2CODE_LIVE_BUILD=1 pytest tests/test_live_agent.py -q -s     # opt-in live build of the canary
```

and a sentence under "Money and secrets" style notes: the agent builder uses the logged-in `claude` binary or `CLAUDE_CODE_OAUTH_TOKEN`; never set `ANTHROPIC_API_KEY`; in local mode the agent's shell runs on this machine with credentials scrubbed, so use it only for assignments you trust.

Append the step 4a entry to `decisions.md` from the ledger (what was built, the lockdown and why, the live result, surprises, rulings).

- [ ] **Step 4: Commit**

```bash
git add tests/test_live_agent.py README.md decisions.md
git commit -m "Live agent build test (opt-in), README and journal for step 4a

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

**Spec coverage for step 4 (local half).** 9.2 tools: six custom tools, `run_tests` and `give_up` manager-owned, only `run_tests` results count, history written by the manager (Tasks 3, 4). 9.3 caps and "manager ends the session" (Tasks 1, 3, 4: the tool layer and the hook refuse everything after `finished`). 9.3 rate limit → `error/rate_limited`, no retry, workspace and log preserved (Task 5). 9.4 compaction: result text restates failing tests and the last three attempts; `PreCompact` logs (Tasks 3, 4). 2.1 subscription auth and blank API key (Task 4). 15 rate-limit canary (Task 5). 13 `manager/caps.py` (Task 1). Not in this plan: 9.1 Modal sandbox, GPU `run_tests`, network restriction (step 4b); 9.3 wall-clock tier defaults by Max tier (config already has `max_tier`; tuning deferred).

**Deviations recorded.** The paper's full text is not given to the builder (the assignment is the spec and interface; the paper is 80k characters and the spec is meant to be sufficient). Built-in SDK tools are replaced by proxied tools so the SDK and tokens stay on the manager side (stronger than the spec's "shell inside the sandbox" framing, same effect). `BuildContext` gains `session`. New build-log events: `session_init`, `tool_call`, `tool_refused`, `assistant_text`, `usage`, `compaction`, `rate_limited`.

**Type consistency checked.** `BuildSession(record, runner, log, gpu_usd_per_hour, now)` (Task 1) is constructed that way in Tasks 3, 4 tests. `BuilderTools(session, workspace, log, tool_timeout_s)` and `.call(name, args) -> (str, bool)` (Task 3) are used by Task 4. `build_options(tools, config, session, log, cwd)` and `drive(prompt, session, options, client_factory, log)` are consistent between Task 4's implementation and tests. `format_run_tests(result, session)` uses `session.failing_history` and `session.last_result` from Task 1.

**Review Focus pinned.** 1 → Task 4 `test_tools_are_refused_after_session_ends` (and Task 3's tool-layer refusal); 2 → Task 2 `test_paths_outside_root_are_refused`; 3 → Task 5 `test_rate_limit_canary_preserves_workspace_and_log` and Task 4 `test_rate_limit_event_raises_rate_limited`; 4 → Task 1 `test_session_resumes_counters_from_record_and_log`; 5 → Task 4 `test_session_aborts_if_extra_tools_are_visible`.
