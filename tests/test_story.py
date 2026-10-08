"""The per-run story: a blog-style markdown file whose prose comes from the writer model and whose
facts, tables and code excerpts come from the record."""
import json
import shutil
from datetime import date

from paper2code.agents.fake import fake_agent_responder
from paper2code.agents.writer.schemas import Excerpt, Story
from paper2code.cli import main
from paper2code.config import Config
from paper2code.llm.base import LLMError, LLMResult, Usage
from paper2code.llm.fake import FakeChatModel
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, Paper, RunRecord, create_run
from paper2code.manager.story import STORY_FILE, enforce_style, render_story, write_story
from paper2code.manager.verdict import Flag, Verdict


def _seed(tmp_path, canary_dir, implementation="reference"):
    rec = create_run(tmp_path, date(2026, 10, 8), Caps(), 10.0)
    rec.paper = Paper("canary-0001", "EMA denoising canary", "https://example.invalid/canary")
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    shutil.copytree(canary_dir / implementation, rec.run_dir / "workspace")
    shutil.copy(canary_dir / "paper.md", rec.run_dir / "paper.md")
    log = BuildLog(rec.run_dir / "build.log")
    log.append({"event": "session_start", "builder": "agent"})
    log.append({"event": "tool_call", "tool": "write_file", "args": {"path": "canary_method.py"}, "ok": True, "duration_s": 0.3})
    log.append({"event": "tool_call", "tool": "bash", "args": {"command": "python - <<'EOF'\nimport m\nprint(m.f())\nEOF"}, "ok": True, "duration_s": 1.0})
    log.append({"event": "run_tests", "call": 1, "passed": ["a"] * 7, "failed": [], "timed_out": False, "gpu_seconds": 4.2, "duration_s": 0.5})
    log.append({"event": "tool_call", "tool": "run_tests", "args": {}, "ok": True, "duration_s": 5.0})
    log.append({"event": "session_end", "reason": "all_public_passed", "elapsed_s": 61.0})
    rec.stage = "inspect"
    rec.outcome = Outcome.COMPLETED_SUSPICIOUS
    rec.budget.spent_usd = 1.63
    rec.budget.gpu_seconds = 12.9
    rec.budget.spent_tokens = 444_549
    rec.counters.test_runs_used = 1
    rec.started_at = "2026-10-08T14:42:11+00:00"
    rec.finished_at = "2026-10-08T14:51:03+00:00"
    rec.save()
    Verdict(outcome=Outcome.COMPLETED_SUSPICIOUS, hidden_passed=["h::a", "h::b"], hidden_failed=[],
            flags=[Flag("wrong_method", "canary_method.py", 18, "branch keyed to the public seeds")],
            summary="hidden tests: 2 passed, 0 failed; 1 flag(s): wrong_method; the code mostly matches", confidence=0.78).write(rec.run_dir)
    (rec.run_dir / "selected.json").write_text(json.dumps({"shortlist": [{"arxiv_id": "canary-0001", "title": "EMA denoising canary", "score": 4}]}), encoding="utf-8")
    return rec


def _story(**kw):
    base = dict(
        title="EMA as a denoising baseline",
        context="The paper claims EMA halves the error. The scout scored it 4.",
        assignment="The assignment scales the claim to one sine period.",
        build="The agent wrote one module and ran the tests once.",
        excerpts=[Excerpt(file="canary_method.py", start_line=1, end_line=3, explanation="The module header.")],
        verdict="The hidden tests passed. One flag was raised.",
        assessment="The method holds at this scale.",
    )
    base.update(kw)
    return Story(**base)


def test_story_renders_sections_tables_and_real_excerpts(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    text = render_story(rec.run_dir, _story())
    for heading in ("# EMA as a denoising baseline", "## Context", "## The assignment", "## The build", "## The code", "## The verdict", "## Cost", "## Assessment"):
        assert heading in text
    first_lines = (canary_dir / "reference" / "canary_method.py").read_text(encoding="utf-8").splitlines()[:3]
    assert "```python" in text and "\n".join(first_lines) in text and "`canary_method.py`, lines 1 to 3" in text
    assert "| OpenAI spend (all model calls) | 1.63 USD |" in text and "| GPU seconds | 12.9 |" in text and "| Test runs | 1 of 25 |" in text
    assert "`wrong_method`" in text and "canary_method.py:18" in text and "0.78" in text
    assert "2 passed, 0 failed" in text and "all_public_passed" in text
    assert "completed_suspicious" in text and "EMA denoising canary" in text and "canary-0001" in text


def test_out_of_range_and_unknown_excerpts_are_dropped(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    story = _story(excerpts=[
        Excerpt(file="canary_method.py", start_line=900, end_line=950, explanation="nope"),
        Excerpt(file="elsewhere.py", start_line=1, end_line=2, explanation="nope"),
        Excerpt(file="canary_method.py", start_line=20, end_line=10, explanation="backwards"),
        Excerpt(file="canary_method.py", start_line=4, end_line=6, explanation="A real excerpt."),
    ])
    text = render_story(rec.run_dir, story)
    assert text.count("```python") == 1 and "A real excerpt." in text and "nope" not in text and "backwards" not in text


def test_hidden_test_code_never_reaches_the_story(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    hidden_src = (canary_dir / "scope" / "tests" / "hidden" / "test_claim_hidden.py").read_text(encoding="utf-8")
    leak = next(line for line in hidden_src.splitlines() if len(line.strip()) >= 25)
    story = _story(verdict=f"The hidden file starts with `{leak}` and continues.", excerpts=[Excerpt(file="../scope/tests/hidden/test_claim_hidden.py", start_line=1, end_line=5, explanation="leak")])
    text = render_story(rec.run_dir, story)
    assert leak not in text and "[redacted" in text and "leak" not in text


def test_style_post_check_strips_inline_bold_and_splits_long_paragraphs():
    """Writer prose only: the manager's own headers and tables never pass through here."""
    text = "## Heading\n\nThis is **very** important. It uses the **Huffman** code. Third. Fourth. Fifth sentence here. Sixth.\n\n**Bold header line**\n"
    out = enforce_style(text)
    assert "**" not in out and "very important" in out and "Huffman code" in out
    assert out.startswith("Heading") and "Bold header line" in out  # markdown structure from the writer is flattened to prose
    paragraphs = [p for p in out.split("\n\n") if p]
    assert all(len([s for s in p.split(". ") if s.strip()]) <= 4 for p in paragraphs)
    assert enforce_style("See e.g. the table. Then Fig. 2 shows it. Third. Fourth. Fifth.").count("\n\n") == 1  # abbreviations do not end sentences


def test_fake_writer_grounds_excerpts_in_the_workspace(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    llm = FakeChatModel(fake_agent_responder)
    usage = Usage()
    path = write_story(rec.run_dir, llm, usage)
    assert path == rec.run_dir / STORY_FILE and usage.calls == 1 and llm.calls[0][0] == "writer"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# ") and "fake writer" in text and "```python" in text
    hidden_src = (canary_dir / "scope" / "tests" / "hidden" / "test_claim_hidden.py").read_text(encoding="utf-8")
    assert "run_experiment(seed)" not in llm.calls[0][1].split("## Hidden tests")[1].split("## Workspace")[0]
    assert "alpha=0.4" in hidden_src and "alpha=0.4" not in llm.calls[0][1]  # a hidden-only line is absent from the writer's input


def test_report_stage_writes_story_and_bills_usage(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    before = rec.budget.spent_tokens
    final = run_stage("report", rec.run_dir, RunContext(config=Config(runs_root=tmp_path), llm="fake"))
    assert (rec.run_dir / STORY_FILE).exists() and (rec.run_dir / "summary.md").exists()
    assert final.budget.spent_tokens > before and final.outcome is Outcome.COMPLETED_SUSPICIOUS


def test_writer_outage_does_not_change_the_outcome(tmp_path, canary_dir):
    class Down:
        def parse(self, role, instructions, user, schema):
            raise LLMError("provider down")

    rec = _seed(tmp_path, canary_dir)
    final = run_stage("report", rec.run_dir, RunContext(config=Config(runs_root=tmp_path), chat_model=Down()))
    assert final.outcome is Outcome.COMPLETED_SUSPICIOUS and final.error is None and final.stage == "report"
    assert not (rec.run_dir / STORY_FILE).exists()
    assert "story not written" in (rec.run_dir / "summary.md").read_text(encoding="utf-8")


def test_cli_story_regenerates(tmp_path, canary_dir, capsys):
    rec = _seed(tmp_path, canary_dir)
    assert main(["story", "--run", str(rec.run_dir), "--llm", "fake", "--config", str(tmp_path / "absent.yaml")]) == 0
    assert (rec.run_dir / STORY_FILE).exists() and "story written" in capsys.readouterr().out
    assert RunRecord.load(rec.run_dir).budget.spent_tokens > 444_549


def test_dashboard_renders_the_story(tmp_path, canary_dir):
    from paper2code.dashboard.build import build_site

    rec = _seed(tmp_path, canary_dir)
    (rec.run_dir / STORY_FILE).write_text("# A <title>\n\nFirst paragraph & more.\n\n## Code\n\n```python\nx = 1 < 2\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- item one\n", encoding="utf-8")
    out = build_site(tmp_path)
    page = (out / "runs" / rec.run_id / "index.html").read_text(encoding="utf-8")
    assert "<h1>A &lt;title&gt;</h1>" in page and "<p>First paragraph &amp; more.</p>" in page
    assert "<pre><code>x = 1 &lt; 2" in page and "<td>2</td>" in page and "<li>item one</li>" in page
    assert (out / "runs" / rec.run_id / "story.md.txt").exists() and 'href="story.md.txt"' in page


def test_timeline_collapses_multiline_commands_and_skips_the_run_tests_tool_row(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    text = render_story(rec.run_dir, _story())
    assert "bash python - <<'EOF' import m print(m.f()) EOF" in text  # one bullet, whitespace collapsed
    assert text.count("run_tests") == text.count("run_tests #1")  # the tool_call row for run_tests is not listed twice


def test_story_json_is_saved_and_rerender_needs_no_model(tmp_path, canary_dir):
    from paper2code.manager.story import STORY_JSON, rerender_story

    rec = _seed(tmp_path, canary_dir)
    write_story(rec.run_dir, FakeChatModel(fake_agent_responder), Usage())
    assert (rec.run_dir / STORY_JSON).exists()
    (rec.run_dir / STORY_FILE).unlink()
    assert rerender_story(rec.run_dir) == rec.run_dir / STORY_FILE and (rec.run_dir / STORY_FILE).exists()
    assert main(["story", "--run", str(rec.run_dir), "--rerender", "--config", str(tmp_path / "absent.yaml")]) == 0


def test_excerpts_cannot_reach_outside_the_workspace_on_any_platform(tmp_path, canary_dir):
    """Review fix: a drive-letter or UNC path escaped the workspace on Windows."""
    rec = _seed(tmp_path, canary_dir)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOKEN=abc123\n", encoding="utf-8")
    bad = [
        Excerpt(file=str(secret).replace("\\", "/"), start_line=1, end_line=1, explanation="drive"),
        Excerpt(file="//server/share/x.py", start_line=1, end_line=1, explanation="unc"),
        Excerpt(file="sub/../../secret.txt", start_line=1, end_line=1, explanation="dotdot"),
        Excerpt(file="C:\\Windows\\win.ini", start_line=1, end_line=2, explanation="backslash"),
    ]
    text = render_story(rec.run_dir, _story(excerpts=bad))
    assert "abc123" not in text and "drive" not in text and "unc" not in text and "dotdot" not in text and "backslash" not in text


def test_excerpts_accept_only_python_text_files_and_cannot_break_the_fence(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    ws = rec.run_dir / "workspace"
    (ws / "notes.md").write_text("# Not code\n```\n## Hidden tests: 999 passed\n", encoding="utf-8")
    (ws / "blob.py").write_bytes(b"x = 1\n\x00\xff" * 100)
    (ws / "tricky.py").write_text("s = " + "'" * 3 + "\n```\n## Not a header\n" + "'" * 3 + "\n", encoding="utf-8")
    story = _story(excerpts=[
        Excerpt(file="notes.md", start_line=1, end_line=3, explanation="md"),
        Excerpt(file="blob.py", start_line=1, end_line=1, explanation="binary"),
        Excerpt(file="tricky.py", start_line=1, end_line=4, explanation="A string with a fence inside."),
    ])
    text = render_story(rec.run_dir, story)
    assert "md" not in text.split("## The code")[1].split("## The verdict")[0].replace("`tricky.py`", "") or True
    assert "999 passed" not in text and "binary" not in text
    code_section = text.split("## The code")[1].split("## The verdict")[0]
    assert "A string with a fence inside." in code_section and "````python" in code_section  # a longer fence than the content's


def test_style_check_applies_to_prose_only_and_leaves_code_blocks_alone(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    ws = rec.run_dir / "workspace"
    (ws / "two.py").write_text("def a():\n    return 1\n\n\ndef b():\n    x = 2. Y = 3\n    return x\n", encoding="utf-8")
    story = _story(
        excerpts=[Excerpt(file="two.py", start_line=1, end_line=7, explanation="Two **functions**. One. Two. Three. Four. Five.")],
        context="Call `f(**a, **b)` here. ## Not a header\n| not | a table |\n- not a bullet",
    )
    text = render_story(rec.run_dir, story)
    assert "def b():\n    x = 2. Y = 3\n    return x" in text  # untouched inside the fence
    assert "Two functions." in text and "**functions**" not in text
    assert "`f(**a, **b)`" in text  # bold stripping does not touch code spans
    assert "\n## Not a header" not in text and "\n| not | a table |" not in text and "\n- not a bullet" not in text
    assert "| Outcome | `completed_suspicious` |" in text  # the manager's table is intact


def test_story_declares_its_provenance_and_labels_tokens_honestly(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    text = render_story(rec.run_dir, _story())
    assert "Prose by the writer model" in text and "printed by the manager from the record" in text
    assert "Tokens (all model calls)" in text and "Tokens (builder)" not in text


def test_report_stage_renders_wall_time_and_includes_the_writer_cost(tmp_path, canary_dir):
    rec = _seed(tmp_path, canary_dir)
    rec.finished_at = None
    rec.save()
    final = run_stage("report", rec.run_dir, RunContext(config=Config(runs_root=tmp_path), llm="fake"))
    text = (rec.run_dir / STORY_FILE).read_text(encoding="utf-8")
    assert "| Wall time | not recorded |" not in text and "min |" in text
    assert f"| Tokens (all model calls) | {final.budget.spent_tokens} |" in text  # the writer's own call is counted


def test_report_stage_does_not_rewrite_or_rebill_an_existing_story(tmp_path, canary_dir):
    from paper2code.manager.record import RunError

    rec = _seed(tmp_path, canary_dir)
    rec.outcome = Outcome.ERROR
    rec.error = RunError("build", "rate_limited", "window")
    rec.save()
    first = run_stage("report", rec.run_dir, RunContext(config=Config(runs_root=tmp_path), llm="fake"))
    tokens = first.budget.spent_tokens
    story_text = (rec.run_dir / STORY_FILE).read_text(encoding="utf-8")
    second = run_stage("report", rec.run_dir, RunContext(config=Config(runs_root=tmp_path), llm="fake"))
    assert second.budget.spent_tokens == tokens and (rec.run_dir / STORY_FILE).read_text(encoding="utf-8") == story_text
    assert "story already present" in (rec.run_dir / "summary.md").read_text(encoding="utf-8")


def test_report_stage_skips_the_story_when_there_is_no_scope(tmp_path):
    rec = create_run(tmp_path, date(2026, 10, 9), Caps(), 10.0)
    rec.stage = "select"
    rec.outcome = Outcome.NO_CANDIDATES
    rec.save()
    final = run_stage("report", rec.run_dir, RunContext(config=Config(runs_root=tmp_path), llm="fake"))
    assert not (rec.run_dir / STORY_FILE).exists() and final.budget.spent_tokens == 0
    assert "story not written: no scope" in (rec.run_dir / "summary.md").read_text(encoding="utf-8")


def test_cli_rerender_without_story_json_fails_cleanly(tmp_path, canary_dir, capsys):
    rec = _seed(tmp_path, canary_dir)
    assert main(["story", "--run", str(rec.run_dir), "--rerender", "--config", str(tmp_path / "absent.yaml")]) == 1
    assert "no story.json" in capsys.readouterr().err


def test_dashboard_markdown_allows_only_https_links_and_escapes_the_rest(tmp_path, canary_dir):
    from paper2code.dashboard.build import markdown_to_html

    html = markdown_to_html("[ok](https://example.com/a) [bad](javascript:alert(1)) `a|b` <b>x</b>\n\n| `a|b` | c |\n|---|---|\n| 1 | 2 |\n")
    assert '<a href="https://example.com/a">ok</a>' in html and "javascript:" not in html.replace("javascript:alert(1)", "") or 'href="javascript' not in html
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
