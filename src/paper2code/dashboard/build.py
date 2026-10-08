"""Static dashboard over the runs root (spec 11): an index of every run with outcome and cost, and one
page per run with the record's files. Pure Python, no JavaScript, every string escaped; hidden tests
are never copied because the site may be public."""
from __future__ import annotations

import html
import shutil
from dataclasses import dataclass
from pathlib import Path

from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import VERDICT_JSON, Verdict

SITE_DIR = "docs"
_DRILLDOWN = (
    "run.json", "summary.md", "story.md", "verdict.json", "build.log", "candidates.jsonl", "selected.json",
    "scope_attempts.jsonl", "scope/spec.md", "scope/interface.md", "scope/manifest.json",
)
_DRILLDOWN_GLOBS = ("scope/tests/public/*.py", "workspace/**/*.py")
_CSS = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.4rem .6rem;border-bottom:1px solid #ddd;vertical-align:top}
.badge{padding:.1rem .5rem;border-radius:.6rem;font-size:.85em;background:#eee;white-space:nowrap}
.completed{background:#d8f3dc}.completed_suspicious{background:#fff3bf}.hidden_failed,.tests_tampered{background:#ffd6d6}
.incomplete_budget,.incomplete_stuck{background:#e7e5ff}.error,.unreadable{background:#f1f1f1}.scope_rejected,.no_candidates{background:#f8f0e3}
pre{background:#f6f6f6;padding:.8rem;overflow:auto;white-space:pre-wrap}
"""


@dataclass
class RunRow:
    run_id: str
    run_dir: Path
    outcome: str
    stage: str
    paper_title: str
    paper_url: str
    arxiv_id: str
    spent_usd: float
    gpu_seconds: float
    test_runs_used: int
    flags: int
    confidence: float | None
    started_at: str
    finished_at: str
    error: str


def _esc(text: object) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def collect_runs(runs_root: Path) -> list[RunRow]:
    """One row per directory holding a run.json, newest first; unreadable records become a row too."""
    rows: list[RunRow] = []
    if not runs_root.exists():
        return rows
    run_dirs = sorted((p for p in runs_root.iterdir() if p.is_dir() and (p / "run.json").exists()), reverse=True)
    for run_dir in run_dirs:
        try:
            rec = RunRecord.load(run_dir)
        except Exception as exc:  # a half-written or foreign run.json must not hide the other runs
            rows.append(RunRow(run_dir.name, run_dir, "unreadable", "", "", "", "", 0.0, 0.0, 0, 0, None, "", "", f"{type(exc).__name__}: {exc}"))
            continue
        flags, confidence = 0, None
        if (run_dir / VERDICT_JSON).exists():
            try:
                v = Verdict.load(run_dir)
                flags, confidence = len(v.flags), v.confidence
            except Exception:
                pass
        rows.append(RunRow(
            rec.run_id, run_dir, rec.outcome.value if rec.outcome else "in progress", rec.stage or "",
            rec.paper.title, rec.paper.url, rec.paper.arxiv_id, rec.budget.spent_usd, rec.budget.gpu_seconds,
            rec.counters.test_runs_used, flags, confidence, rec.started_at or "", rec.finished_at or "",
            f"{rec.error.stage}/{rec.error.reason}: {rec.error.message}" if rec.error else "",
        ))
    return rows


def markdown_to_html(text: str) -> str:
    """Enough Markdown for story.md: headers, paragraphs, fenced code, tables, bullet lists, inline code.
    Everything is escaped first; no raw HTML passes through."""
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            out.append("<pre><code>" + _esc("\n".join(code)) + "</code></pre>")
            i += 1
            continue
        if line.startswith("#"):
            flush()
            level = min(len(line) - len(line.lstrip("#")), 6)
            out.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
            i += 1
            continue
        if line.startswith("|"):
            flush()
            rows: list[str] = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(lines[i])
                i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            body = [r for r in cells if not all(set(c) <= set("-: ") for c in r)]
            html_rows = []
            for n, r in enumerate(body):
                tag = "th" if n == 0 else "td"
                html_rows.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in r) + "</tr>")
            out.append("<table>" + "".join(html_rows) + "</table>")
            continue
        if line.startswith(("- ", "* ")):
            flush()
            items: list[str] = []
            while i < len(lines) and lines[i].startswith(("- ", "* ")):
                items.append(f"<li>{_inline(lines[i][2:].strip())}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        paragraph.append(line.strip())
        i += 1
    flush()
    return "\n".join(out)


def _inline(text: str) -> str:
    """Escape, then allow inline code and links only."""
    import re

    escaped = _esc(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def _page(title: str, body: str) -> str:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>"
    )


def _index_html(rows: list[RunRow]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    total = sum(r.spent_usd for r in rows)
    head = " ".join(f'<span class="badge {_esc(k)}">{_esc(k)}: {v}</span>' for k, v in sorted(counts.items()))
    lines = [
        f"<h1>paper2code runs</h1><p>{len(rows)} runs, {total:.2f} USD of model spend. {head}</p>",
        "<table><tr><th>run</th><th>paper</th><th>outcome</th><th>stage</th><th>USD</th><th>GPU s</th>"
        "<th>test runs</th><th>flags</th><th>confidence</th></tr>",
    ]
    for r in rows:
        paper = f'<a href="{_esc(r.paper_url)}">{_esc(r.paper_title)}</a>' if r.paper_url else _esc(r.paper_title)
        conf = f"{r.confidence:.2f}" if r.confidence is not None else ""
        err = f"<br><small>{_esc(r.error)}</small>" if r.error else ""
        lines.append(
            f'<tr><td><a href="runs/{_esc(r.run_id)}/index.html">{_esc(r.run_id)}</a></td><td>{paper}</td>'
            f'<td><span class="badge {_esc(r.outcome)}">{_esc(r.outcome)}</span>{err}</td><td>{_esc(r.stage)}</td>'
            f"<td>{r.spent_usd:.2f}</td><td>{r.gpu_seconds:.0f}</td><td>{r.test_runs_used}</td><td>{r.flags}</td><td>{conf}</td></tr>"
        )
    lines.append("</table>")
    return _page("paper2code runs", "\n".join(lines))


def _copy_drilldown(run_dir: Path, out: Path) -> list[str]:
    """Copy the run's text files next to the page as .txt; never anything under a `hidden` directory."""
    copied: list[str] = []
    candidates = [run_dir / rel for rel in _DRILLDOWN]
    for pattern in _DRILLDOWN_GLOBS:
        candidates += sorted(run_dir.glob(pattern))
    for src in candidates:
        if not src.is_file() or "hidden" in src.relative_to(run_dir).parts:
            continue
        rel = src.relative_to(run_dir).as_posix() + ".txt"
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied.append(rel)
    return copied


def _run_html(row: RunRow, files: list[str]) -> str:
    parts = [
        f'<p><a href="../../index.html">all runs</a></p><h1>Run {_esc(row.run_id)}</h1>',
        f'<p><span class="badge {_esc(row.outcome)}">{_esc(row.outcome)}</span> stage {_esc(row.stage)} '
        f"| {row.spent_usd:.2f} USD | {row.gpu_seconds:.0f} GPU s | {row.test_runs_used} test runs "
        f"| started {_esc(row.started_at)} | finished {_esc(row.finished_at)}</p>",
    ]
    if row.paper_title or row.arxiv_id:
        link = f'<a href="{_esc(row.paper_url)}">{_esc(row.paper_title)}</a>' if row.paper_url else _esc(row.paper_title)
        parts.append(f"<p>Paper: {link} ({_esc(row.arxiv_id)})</p>")
    if row.error:
        parts.append(f"<p><b>Error:</b> {_esc(row.error)}</p>")
    story = row.run_dir / "story.md"
    if story.exists():
        parts.append("<h2>Story</h2>")
        parts.append(markdown_to_html(story.read_text(encoding="utf-8", errors="replace")))
    summary = row.run_dir / "summary.md"
    parts.append("<h2>Summary</h2>")
    if summary.exists():
        parts.append(f"<pre>{_esc(summary.read_text(encoding='utf-8', errors='replace'))}</pre>")
    else:
        parts.append("<p>no summary (run ended before the report stage)</p>")
    parts.append("<h2>Verdict</h2>")
    if (row.run_dir / VERDICT_JSON).exists():
        try:
            v = Verdict.load(row.run_dir)
            parts.append(f"<p>{_esc(v.summary)}</p>")
            if v.confidence is not None:
                parts.append(f"<p>Inspector confidence: {v.confidence:.2f}</p>")
            parts.append(f"<p>Hidden tests: {len(v.hidden_passed)} passed, {len(v.hidden_failed)} failed</p>")
            if v.flags:
                items = "".join(
                    f"<li><code>{_esc(f.kind)}</code> ({_esc(f.source)}) at {_esc(f.file)}:{_esc(f.line)}: {_esc(f.note)}</li>" for f in v.flags
                )
                parts.append(f"<ul>{items}</ul>")
        except Exception as exc:
            parts.append(f"<p>verdict unreadable: {_esc(type(exc).__name__)}</p>")
    else:
        parts.append("<p>no verdict (run ended before inspection)</p>")
    parts.append("<h2>Files</h2><ul>" + "".join(f'<li><a href="{_esc(f)}">{_esc(f[:-4])}</a></li>' for f in files) + "</ul>")
    return _page(f"Run {row.run_id}", "\n".join(parts))


def build_site(runs_root: Path, out_dir: Path | None = None) -> Path:
    """Write index.html and runs/<run_id>/index.html (plus drill-down copies) under out_dir (default runs_root/docs)."""
    out = out_dir or runs_root / SITE_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")  # GitHub Pages: serve files whose names start with _ or .
    rows = collect_runs(runs_root)
    (out / "index.html").write_text(_index_html(rows), encoding="utf-8")
    for row in rows:
        run_out = out / "runs" / row.run_id
        if run_out.exists():
            shutil.rmtree(run_out)
        run_out.mkdir(parents=True)
        files = _copy_drilldown(row.run_dir, run_out) if row.outcome != "unreadable" else []
        (run_out / "index.html").write_text(_run_html(row, files), encoding="utf-8")
    return out
