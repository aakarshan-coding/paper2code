"""The run's story: one blog-style markdown file per run. The writer model supplies the prose; every
number, table, test result and code line comes from the record, rendered here.

Invariants: the style post-check touches only the writer's prose, never the manager's lines or the
code; an excerpt is a `.py` text file inside the workspace, fenced so its content cannot close the
fence; hidden test code never reaches the file, by input (the writer does not see it) and by
redaction (a prose line that contains a hidden source line is replaced)."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from paper2code.agents.inspector.bundle import build_bundle
from paper2code.agents.writer.schemas import Excerpt, Story
from paper2code.agents.writer.writer import write_story_with_model
from paper2code.llm.base import ChatModel, Usage
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import VERDICT_JSON, Verdict

STORY_FILE = "story.md"
STORY_JSON = "story.json"  # the writer's output, kept so the file can be re-rendered without a model call
MAX_EXCERPT_LINES = 60
MAX_EXCERPT_CHARS = 6000
MIN_REDACT_CHARS = 20
PROVENANCE = ("Prose by the writer model; every number, table, timeline row and code line below is printed by "
              "the manager from the record.")
_CODE_SPAN = re.compile(r"`[^`\n]*`")
_INLINE_BOLD = re.compile(r"\*\*(?!\s)([^*\n]+?)\*\*")
_ABBREVIATIONS = ("e.g.", "i.e.", "vs.", "fig.", "dr.", "et al.", "no.", "cf.", "approx.")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`(\"'])")
_BACKTICK_RUN = re.compile(r"`{3,}")


# ---- facts from the record ---------------------------------------------------------------------
def _scout_score(run_dir: Path, arxiv_id: str) -> str:
    path = run_dir / "selected.json"
    if not path.exists():
        return "not recorded"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return "not recorded"
    if not isinstance(data, dict):
        return "not recorded"
    for row in data.get("shortlist", []):
        if isinstance(row, dict) and row.get("arxiv_id") == arxiv_id:
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
    return f"{(end - start).total_seconds() / 60:.0f} min"


def _timeline(rows: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = row.get("event")
        ts = str(row.get("ts", ""))[11:19]
        if event == "tool_call":
            if row.get("tool") == "run_tests":
                continue  # the run_tests event itself carries the result
            args = row.get("args") or {}
            detail = " ".join(str(args.get("path") or args.get("command") or "").split()) if isinstance(args, dict) else ""
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


def collect_facts(run_dir: Path, record: RunRecord | None = None) -> dict:
    """Authoritative facts. `record` is the in-memory record when the caller is mid-stage (report sets
    finished_at before saving); otherwise run.json on disk."""
    record = record or RunRecord.load(run_dir)
    verdict = Verdict.load(run_dir) if (run_dir / VERDICT_JSON).exists() else None
    timeline = _timeline(BuildLog(run_dir / "build.log").read())
    facts = {"record": record, "verdict": verdict, "timeline": timeline,
             "scout_score": _scout_score(run_dir, record.paper.arxiv_id), "wall_time": _wall_time(record)}
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


# ---- prose hygiene (writer text only) ----------------------------------------------------------
def _hidden_lines(run_dir: Path) -> set[str]:
    hidden = run_dir / "scope" / "tests" / "hidden"
    out: set[str] = set()
    if hidden.exists():
        for path in hidden.rglob("*.py"):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if len(line.strip()) >= MIN_REDACT_CHARS:
                    out.add(line.strip())
    return out


def _strip_bold_outside_code(text: str) -> str:
    parts, last = [], 0
    for m in _CODE_SPAN.finditer(text):
        parts.append(_INLINE_BOLD.sub(r"\1", text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(_INLINE_BOLD.sub(r"\1", text[last:]))
    return "".join(parts)


def _split_sentences(paragraph: str) -> list[str]:
    pieces = _SENTENCE_END.split(paragraph)
    merged: list[str] = []
    for piece in pieces:
        if merged and merged[-1].lower().endswith(_ABBREVIATIONS):
            merged[-1] += " " + piece
        else:
            merged.append(piece)
    return merged


def enforce_style(text: str, hidden_lines: set[str] | None = None) -> str:
    """Writer prose only: drop markdown structure the writer must not emit, strip inline bold outside
    code spans, break paragraphs longer than four sentences, redact any line quoting a hidden test."""
    hidden_lines = hidden_lines or set()
    paragraphs: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n\n"):
        kept: list[str] = []
        for line in paragraph.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if any(h in stripped for h in hidden_lines):
                kept.append("[redacted: hidden test content]")
                continue
            stripped = re.sub(r"^(#{1,6}\s+|\|\s*|[-*]\s+|```.*)", "", stripped).rstrip("|").strip()
            if stripped:
                kept.append(stripped)
        if not kept:
            continue
        flat = _strip_bold_outside_code(" ".join(kept))
        sentences = _split_sentences(flat)
        paragraphs += [" ".join(sentences[i:i + 4]) for i in range(0, len(sentences), 4)]
    return "\n\n".join(paragraphs)


# ---- excerpts ----------------------------------------------------------------------------------
def _excerpt_block(workspace: Path, excerpt: Excerpt) -> str | None:
    rel = excerpt.file.replace("\\", "/")
    if PureWindowsPath(rel).is_absolute() or PurePosixPath(rel).is_absolute() or rel.startswith("//") or ":" in rel:
        return None
    parts = PurePosixPath(rel).parts
    if ".." in parts or "hidden" in parts or not rel.endswith(".py"):
        return None
    try:
        base = workspace.resolve()
        path = (workspace / rel).resolve()
    except OSError:
        return None
    if base not in path.parents or not path.is_file():
        return None
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        return None
    lines = data.decode("utf-8", errors="replace").splitlines()
    start, end = excerpt.start_line, excerpt.end_line
    if start > end or start > len(lines):
        return None
    capped_end = min(end, len(lines), start + MAX_EXCERPT_LINES - 1)
    code = "\n".join(lines[start - 1:capped_end])
    if len(code) > MAX_EXCERPT_CHARS:
        code = code[:MAX_EXCERPT_CHARS] + "\n# [cut at 6000 characters]"
    longest = max((len(m.group(0)) for m in _BACKTICK_RUN.finditer(code)), default=0)
    fence = "`" * max(3, longest + 1)
    note = f" (cut at {MAX_EXCERPT_LINES} lines)" if capped_end < min(end, len(lines)) else ""
    return f"{excerpt.explanation}\n\n`{rel}`, lines {start} to {capped_end}{note}:\n\n{fence}python\n{code}\n{fence}"


# ---- rendering ---------------------------------------------------------------------------------
def render_story(run_dir: Path, story: Story, record: RunRecord | None = None) -> str:
    facts = collect_facts(run_dir, record)
    record = facts["record"]
    verdict: Verdict | None = facts["verdict"]
    hidden = _hidden_lines(run_dir)
    prose = {k: enforce_style(getattr(story, k), hidden) for k in ("context", "assignment", "build", "verdict", "assessment")}
    title = enforce_style(story.title, hidden).replace("\n\n", " ").strip() or f"Run {record.run_id}"
    outcome = record.outcome.value if record.outcome else "none"
    paper = f"[{record.paper.title}]({record.paper.url})" if record.paper.url else record.paper.title
    parts = [
        f"# {title}", "",
        f"Run `{record.run_id}` of paper2code on {paper} ({record.paper.arxiv_id}). Outcome: `{outcome}`.", "",
        f"*{PROVENANCE}*", "",
        "## Context", "", prose["context"], "",
        "## The assignment", "", prose["assignment"], "",
    ]
    public_dir, hidden_dir = run_dir / "scope" / "tests" / "public", run_dir / "scope" / "tests" / "hidden"
    public = sorted(p.name for p in public_dir.glob("*.py")) if public_dir.exists() else []
    hidden_count = len(list(hidden_dir.glob("*.py"))) if hidden_dir.exists() else 0
    parts += [f"Public test files: {', '.join(f'`{n}`' for n in public) or 'none'}. Hidden test files: {hidden_count} (not published).", ""]
    parts += ["## The build", "", prose["build"], ""]
    if facts["timeline"]:
        parts += ["Build log, in order:", ""] + [f"- {t}" for t in facts["timeline"]] + [""]
    blocks = []
    for e in story.excerpts:
        cleaned = Excerpt(file=e.file, start_line=e.start_line, end_line=e.end_line, explanation=enforce_style(e.explanation, hidden))
        block = _excerpt_block(run_dir / "workspace", cleaned)
        if block:
            blocks.append(block)
    parts += ["## The code", ""]
    parts += [b + "\n" for b in blocks] if blocks else ["No excerpt was selected.", ""]
    parts += ["## The verdict", "", prose["verdict"], ""]
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
        f"| OpenAI spend (all model calls) | {record.budget.spent_usd:.2f} USD |",
        f"| GPU seconds | {record.budget.gpu_seconds:.1f} |",
        f"| Tokens (all model calls) | {record.budget.spent_tokens} |",
        f"| Wall time | {facts['wall_time']} |",
        f"| Scout score | {facts['scout_score']} |",
        "",
        "## Assessment", "", prose["assessment"], "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def write_story(run_dir: Path, llm: ChatModel, usage: Usage, max_chars: int = 300_000, record: RunRecord | None = None) -> Path:
    """One writer call, then render. When `record` is given its budget is charged before rendering so the
    cost table includes this call; the caller saves the record. ChatModel errors propagate."""
    bundle = build_bundle(run_dir, max_chars)
    facts = collect_facts(run_dir, record)
    story = write_story_with_model(bundle, facts, llm, usage)
    if record is not None:
        record.budget.spent_usd += usage.cost_usd
        record.budget.spent_tokens += usage.input_tokens + usage.output_tokens
    (run_dir / STORY_JSON).write_text(story.model_dump_json(indent=2), encoding="utf-8")
    path = run_dir / STORY_FILE
    path.write_text(render_story(run_dir, story, record), encoding="utf-8")
    return path


def rerender_story(run_dir: Path) -> Path:
    """Render story.md again from the saved story.json (after a renderer change); no model call."""
    source = run_dir / STORY_JSON
    if not source.exists():
        raise FileNotFoundError(f"no {STORY_JSON} in {run_dir}; run `paper2code story --run {run_dir}` first")
    story = Story.model_validate_json(source.read_text(encoding="utf-8"))
    path = run_dir / STORY_FILE
    path.write_text(render_story(run_dir, story), encoding="utf-8")
    return path
