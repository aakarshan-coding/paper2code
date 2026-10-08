"""The run's story: one blog-style markdown file per run. The writer model supplies the prose; every
number, table, test result and code line comes from the record, rendered here. Hidden tests are never
quoted because the file is published."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from paper2code.agents.inspector.bundle import build_bundle
from paper2code.agents.writer.schemas import Story
from paper2code.agents.writer.writer import write_story_with_model
from paper2code.llm.base import ChatModel, Usage
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import VERDICT_JSON, Verdict

STORY_FILE = "story.md"
STORY_JSON = "story.json"  # the writer's output, kept so the file can be re-rendered without a model call
MAX_EXCERPT_LINES = 60
_INLINE_BOLD = re.compile(r"(?<!^)\*\*(?!\s)([^*\n]+?)\*\*", re.MULTILINE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`(\"'])")


# ---- facts from the record ---------------------------------------------------------------------
def _scout_score(run_dir: Path, arxiv_id: str) -> str:
    path = run_dir / "selected.json"
    if not path.exists():
        return "not recorded"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "not recorded"
    rows = data.get("shortlist", data if isinstance(data, list) else [])
    for row in rows:
        if row.get("arxiv_id") == arxiv_id:
            score = row.get("score", row.get("testability"))
            return str(score) if score is not None else "not recorded"
    return "not recorded"


def _wall_time(record: RunRecord) -> str:
    try:
        start = datetime.fromisoformat(record.started_at)
        end = datetime.fromisoformat(record.finished_at) if record.finished_at else None
    except (TypeError, ValueError):
        return "not recorded"
    if end is None:
        return "not recorded"
    minutes = (end - start).total_seconds() / 60
    return f"{minutes:.0f} min"


def _timeline(rows: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        event = row.get("event")
        ts = str(row.get("ts", ""))[11:19]
        if event == "tool_call":
            if row.get("tool") == "run_tests":
                continue  # the run_tests event itself carries the result
            args = row.get("args") or {}
            detail = " ".join(str(args.get("path") or args.get("command") or "").split())  # one line, however long the command
            lines.append(f"{ts} {row.get('tool')} {detail[:80]}".rstrip())
        elif event == "run_tests":
            lines.append(f"{ts} run_tests #{row.get('call', '?')}: {len(row.get('passed', []))} passed, {len(row.get('failed', []))} failed"
                         + (", timed out" if row.get("timed_out") else ""))
        elif event in ("session_start", "session_end", "give_up", "infrastructure_failure"):
            extra = f" ({row['reason']})" if row.get("reason") else ""
            lines.append(f"{ts} {event}{extra}")
        elif event == "sandbox":
            lines.append(f"{ts} sandbox {row.get('action')}")
    return lines


def collect_facts(run_dir: Path) -> dict:
    record = RunRecord.load(run_dir)
    verdict = Verdict.load(run_dir) if (run_dir / VERDICT_JSON).exists() else None
    timeline = _timeline(BuildLog(run_dir / "build.log").read())
    facts = {
        "record": record,
        "verdict": verdict,
        "timeline": timeline,
        "scout_score": _scout_score(run_dir, record.paper.arxiv_id),
        "wall_time": _wall_time(record),
    }
    lines = [
        f"run_id: {record.run_id}",
        f"paper: {record.paper.title} ({record.paper.arxiv_id}) {record.paper.url}",
        f"scout score: {facts['scout_score']}",
        f"outcome: {record.outcome.value if record.outcome else 'none'}; stage reached: {record.stage}",
        f"test runs used: {record.counters.test_runs_used} of {record.caps.test_runs}",
        f"OpenAI spend: {record.budget.spent_usd:.2f} USD; GPU seconds: {record.budget.gpu_seconds:.1f}; tokens: {record.budget.spent_tokens}",
        f"wall time: {facts['wall_time']}",
    ]
    if record.error:
        lines.append(f"error: {record.error.stage}/{record.error.reason}: {record.error.message}")
    if verdict:
        lines.append(f"hidden tests: {len(verdict.hidden_passed)} passed, {len(verdict.hidden_failed)} failed")
        lines.append(f"integrity mismatches: {', '.join(verdict.integrity_mismatches) or 'none'}")
        lines.append(f"inspector confidence: {verdict.confidence}")
        for f in verdict.flags:
            lines.append(f"flag: {f.kind} ({f.source}) at {f.file}:{f.line}: {f.note}")
        lines.append(f"inspector summary: {verdict.summary}")
    lines.append("build timeline:")
    lines += [f"  {t}" for t in timeline] or ["  (no build log)"]
    facts["text"] = "\n".join(lines)
    return facts


# ---- rendering ---------------------------------------------------------------------------------
def enforce_style(text: str) -> str:
    """Strip bold used inside sentences and break paragraphs longer than four sentences."""
    out_paragraphs: list[str] = []
    for paragraph in text.split("\n\n"):
        stripped = paragraph.strip()
        if not stripped or stripped.startswith(("#", "|", "```", "- ", "* ")) or "```" in paragraph:
            out_paragraphs.append(paragraph)
            continue
        if stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") == 2:
            out_paragraphs.append(paragraph)  # a line that is only bold reads as a header
            continue
        paragraph = _INLINE_BOLD.sub(r"\1", paragraph)
        sentences = _SENTENCE_END.split(paragraph.replace("\n", " ").strip())
        chunks = [" ".join(sentences[i:i + 4]) for i in range(0, len(sentences), 4)]
        out_paragraphs.extend(chunks)
    return "\n\n".join(out_paragraphs)


def _excerpt_block(workspace: Path, excerpt) -> str | None:
    rel = excerpt.file.replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/") or "hidden" in rel.split("/"):
        return None
    path = workspace / rel
    if not path.is_file():
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start, end = excerpt.start_line, excerpt.end_line
    if start > end or start > len(lines):
        return None
    end = min(end, len(lines), start + MAX_EXCERPT_LINES - 1)
    code = "\n".join(lines[start - 1:end])
    return f"{excerpt.explanation.strip()}\n\n`{rel}`, lines {start} to {end}:\n\n```python\n{code}\n```"


def render_story(run_dir: Path, story: Story) -> str:
    facts = collect_facts(run_dir)
    record: RunRecord = facts["record"]
    verdict: Verdict | None = facts["verdict"]
    outcome = record.outcome.value if record.outcome else "none"
    paper = f"[{record.paper.title}]({record.paper.url})" if record.paper.url else record.paper.title
    parts = [
        f"# {story.title.strip()}",
        "",
        f"Run `{record.run_id}` of paper2code on {paper} ({record.paper.arxiv_id}). Outcome: `{outcome}`.",
        "",
        "## Context", "", story.context.strip(), "",
        "## The assignment", "", story.assignment.strip(), "",
    ]
    public = sorted(p.name for p in (run_dir / "scope" / "tests" / "public").glob("*.py")) if (run_dir / "scope" / "tests" / "public").exists() else []
    hidden = sorted(p.name for p in (run_dir / "scope" / "tests" / "hidden").glob("*.py")) if (run_dir / "scope" / "tests" / "hidden").exists() else []
    parts += [f"Public test files: {', '.join(f'`{n}`' for n in public) or 'none'}. Hidden test files: {len(hidden)} (not published).", ""]
    parts += ["## The build", "", story.build.strip(), ""]
    if facts["timeline"]:
        parts += ["Build log, in order:", ""] + [f"- {t}" for t in facts["timeline"]] + [""]
    blocks = [b for b in (_excerpt_block(run_dir / "workspace", e) for e in story.excerpts) if b]
    parts += ["## The code", ""]
    parts += [b + "\n" for b in blocks] if blocks else ["No excerpt was selected.", ""]
    parts += ["## The verdict", "", story.verdict.strip(), ""]
    if verdict:
        parts += [f"Hidden tests: {len(verdict.hidden_passed)} passed, {len(verdict.hidden_failed)} failed. "
                  f"Integrity: {', '.join(verdict.integrity_mismatches) or 'scope and workspace hashes match'}."]
        if verdict.confidence is not None:
            parts.append(f"Inspector confidence that the code is the paper's method: {verdict.confidence:.2f}.")
        if verdict.flags:
            parts += ["", "Flags:", ""] + [f"- `{f.kind}` ({f.source}) at {f.file}:{f.line}: {f.note}" for f in verdict.flags]
        parts.append("")
    else:
        parts += ["No verdict was recorded for this run.", ""]
    parts += [
        "## Cost", "",
        "| Item | Value |", "|---|---|",
        f"| Outcome | `{outcome}` |",
        f"| Test runs | {record.counters.test_runs_used} of {record.caps.test_runs} |",
        f"| OpenAI spend | {record.budget.spent_usd:.2f} USD |",
        f"| GPU seconds | {record.budget.gpu_seconds:.1f} |",
        f"| Tokens (builder) | {record.budget.spent_tokens} |",
        f"| Wall time | {facts['wall_time']} |",
        f"| Scout score | {facts['scout_score']} |",
        "",
        "## Assessment", "", story.assessment.strip(), "",
    ]
    return enforce_style("\n".join(parts)).rstrip() + "\n"


def write_story(run_dir: Path, llm: ChatModel, usage: Usage, max_chars: int = 300_000) -> Path:
    """One writer call, then render; raises the ChatModel's errors (the caller decides what they mean)."""
    bundle = build_bundle(run_dir, max_chars)
    facts = collect_facts(run_dir)
    story = write_story_with_model(bundle, facts, llm, usage)
    (run_dir / STORY_JSON).write_text(story.model_dump_json(indent=2), encoding="utf-8")
    path = run_dir / STORY_FILE
    path.write_text(render_story(run_dir, story), encoding="utf-8")
    return path


def rerender_story(run_dir: Path) -> Path:
    """Render story.md again from the saved story.json (after a renderer change); no model call."""
    story = Story.model_validate_json((run_dir / STORY_JSON).read_text(encoding="utf-8"))
    path = run_dir / STORY_FILE
    path.write_text(render_story(run_dir, story), encoding="utf-8")
    return path
