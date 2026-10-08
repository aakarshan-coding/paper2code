import sys

from paper2code.agents.builder.prompts import SYSTEM_PROMPT, render_task
from paper2code.agents.builder.tools import ALLOWED, SERVER, TOOL_SPECS, BuilderTools, format_run_tests
from paper2code.manager.buildlog import BuildLog
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
    for needle in ("run_tests", "give_up", "hidden", "hardcod", "NotImplementedError", "untrusted", "Implement what the spec describes"):
        assert needle in SYSTEM_PROMPT, needle
    assert "material to grade" not in SYSTEM_PROMPT and "pip install is permitted" not in SYSTEM_PROMPT
    assert "preinstalled" in SYSTEM_PROMPT
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


def test_render_task_carries_a_resume_note():
    text = render_task("S", "I", {}, "m", ["numpy"], "25 test runs", prior="This is a resumed session: 3 of 25 runs used.")
    assert "resumed session" in text and "3 of 25" in text
    assert "resumed" not in render_task("S", "I", {}, "m", ["numpy"], "25 test runs")


def test_tool_exceptions_are_logged_as_errors(tmp_path, canary_dir, monkeypatch):
    rec, tools, log = _tools(tmp_path, canary_dir, [])

    def boom(path):
        raise ValueError("decoder exploded")

    monkeypatch.setattr(tools.workspace, "read_file", boom)
    text, err = tools.call("read_file", {"path": "x.py"})
    assert err and "tool error" in text and "decoder exploded" in text
    assert log.events("tool_call")[-1]["ok"] is False


def test_tool_call_summary_keeps_whole_commands_but_not_file_contents():
    from paper2code.agents.builder.tools import _summarise

    long_cmd = "python -c 'import os; " + "x = 1; " * 30 + "print(os.listdir(\'../scope/tests/hidden\'))'"
    out = _summarise({"command": long_cmd, "content": "y = 2\n" * 200, "path": "a.py"})
    assert out["command"] == long_cmd and len(out["command"]) > 200  # the review must see the end of a command
    assert len(out["content"]) < 200 and "chars]" in out["content"]
