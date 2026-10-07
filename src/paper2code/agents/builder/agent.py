"""The Agent SDK builder. Locked down: no built-in tools, strict MCP config, no user settings,
an allowlist of six tools, a deny-by-default permission callback, and a startup check of the
session's advertised tool list. The subscription token is used; any API key is blanked."""
from __future__ import annotations

import asyncio
import os
import re
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
from paper2code.manager.caps import MAX_TURNS_CAP
from paper2code.manager.stages.build import BuildSession
from paper2code.sandbox.workspace import LocalWorkspace

NUDGE = "Continue. Call run_tests when you want the public suite run, or give_up if you cannot make progress."
MAX_NUDGES = 3
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class AgentSessionError(RuntimeError):
    pass


class RateLimited(AgentSessionError):
    pass


def find_cli() -> str | None:
    """A native claude executable. The SDK refuses Windows .cmd shims."""
    cand = os.environ.get("CLAUDE_CODE_EXECPATH")
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
                # Tool work (a shell command, a test run) can take minutes; keep the event loop free so
                # hooks, the SDK reader and interrupt() keep working meanwhile.
                text, err = await asyncio.to_thread(tools.call, _n, dict(args or {}))
                return _text(text, err)

            return handler

        sdk_tools.append(make(spec["name"], spec["description"], spec["schema"]))
    return create_sdk_mcp_server(name=SERVER, version="1.0.0", tools=sdk_tools)


def build_options(tools: BuilderTools, config: Config, session: BuildSession, log: BuildLog, cwd: str):
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

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
    # The allowlist auto-approves our six tools before can_use_tool is consulted; the SDK warns about
    # that. Every call is still gated by the PreToolUse hook above, so the warning is noise. pytest
    # resets warning filters per test, hence the local context rather than a module-level filter.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="can_use_tool will not be invoked")
        return _make_options(model, tools, config, can_use_tool, refuse_after_end, on_compact, cwd)


def _make_options(model, tools, config, can_use_tool, refuse_after_end, on_compact, cwd):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

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


def new_usage() -> dict[str, Any]:
    return {k: 0 for k in _USAGE_KEYS} | {"total_cost_usd": None, "num_turns": 0, "nudges": 0}


async def drive(prompt: str, session: BuildSession, options, client_factory, log: BuildLog, usage: dict[str, Any]) -> None:
    """The message loop. `usage` is updated in place as result messages arrive, so whatever was
    spent before a rate limit or a crash is still recorded by the caller."""
    from claude_agent_sdk import AssistantMessage, RateLimitEvent, ResultMessage, SystemMessage, TextBlock

    interrupted = False
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
                    for key in _USAGE_KEYS:
                        usage[key] += int(u.get(key) or 0)
                    usage["total_cost_usd"] = msg.total_cost_usd
                    usage["num_turns"] += int(msg.num_turns or 0)
                    log.append({
                        "event": "usage", **{k: u.get(k) for k in _USAGE_KEYS},
                        "total_cost_usd": msg.total_cost_usd, "num_turns": msg.num_turns, "duration_ms": msg.duration_ms,
                        "subtype": msg.subtype,
                    })
                    if msg.is_error and msg.subtype == "error_max_turns":
                        if not session.finished:
                            session.finish(MAX_TURNS_CAP)
                    elif msg.is_error and not session.finished:
                        raise AgentSessionError(f"agent session ended with an error: {msg.subtype}")
                if session.finished and not interrupted:
                    # The manager has decided; cut the model's turn short rather than let it keep trying refused tools.
                    interrupted = True
                    try:
                        await client.interrupt()
                    except Exception:
                        pass
            session.check_wall_clock()
            if session.finished or usage["nudges"] >= MAX_NUDGES:
                break
            usage["nudges"] += 1
            await client.query(NUDGE)


def _module_name(interface_md: str) -> str:
    m = re.search(r"Module `(\w+)`", interface_md)
    return m.group(1) if m else "solution"


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
        caps_text = (
            f"{caps.test_runs} test runs, {caps.wall_clock_s / 3600:.1f} hours, "
            f"{session.record.budget.limit_usd:.2f} USD of GPU"
        )
        prior_runs = session.record.counters.test_runs_used
        existing = workspace.list_files()
        prior = ""
        if prior_runs or existing:
            prior = (
                f"This is a resumed session: {prior_runs} of {caps.test_runs} runs used so far. "
                f"The workspace already contains: {', '.join(existing[:50]) or '(nothing)'}. "
                "Read what is there before rewriting it."
            )
        prompt = render_task(
            ctx.spec_path.read_text(encoding="utf-8"), interface_md, public_tests, module,
            self.config.allowed_packages, caps_text, prior=prior,
        )
        usage = new_usage()
        scratch = tempfile.mkdtemp(prefix="p2c-agent-")
        try:
            options = build_options(tools, self.config, session, log, cwd=scratch)
            asyncio.run(drive(prompt, session, options, self.client_factory, log, usage))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
            # Whatever happened (pass, cap, rate limit, crash), the tokens seen so far are recorded.
            session.record.budget.spent_tokens += sum(usage[k] for k in _USAGE_KEYS)
            session.record.save()
