"""The builder's six tools as plain Python, unit-testable without the SDK. The SDK wrapper in
agent.py only marshals arguments and text."""
from __future__ import annotations

import threading
import time
from typing import Any

from paper2code.agents.builder.base import BuildFinished
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.stages.build import BuildSession
from paper2code.sandbox.runner import TestRunResult
from paper2code.sandbox.workspace import InfrastructureError, Workspace, WorkspaceError

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
        self._lock = threading.Lock()  # tool calls run on worker threads; the session is single-threaded

    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """(text for the agent, is_error). Every call is logged, even one that blew up; nothing runs after the session ends."""
        start = time.monotonic()
        with self._lock:
            if self.session.finished:
                text, err = f"The session is over: {self.session.finish_reason}. No further actions are possible.", True
            else:
                try:
                    text, err = self._dispatch(name, args)
                except BuildFinished as exc:
                    text, err = f"The session is over: {exc}.", True
                except WorkspaceError as exc:
                    text, err = str(exc), True
                except InfrastructureError as exc:
                    # The sandbox or the test runner broke, not the agent's command: end the session so
                    # the run is recorded as an infrastructure error instead of looping until a cap.
                    self.session.fail("sandbox_failed", f"{type(exc).__name__}: {exc}")
                    text, err = f"Infrastructure failure, the session is over: {exc}", True
                except Exception as exc:  # never let a tool crash escape unlogged
                    text, err = f"tool error: {type(exc).__name__}: {exc}", True
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
    """What build.log keeps of a tool call: whole commands and paths (the build-log review reads them),
    only the head of file contents."""
    out = {}
    for k, v in args.items():
        s = str(v)
        limit = 120 if k == "content" else 1000
        out[k] = s if len(s) <= limit else s[:limit] + f"... [{len(s)} chars]"
    return out
