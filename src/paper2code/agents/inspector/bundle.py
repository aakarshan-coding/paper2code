"""Everything the inspector reads, as text, capped. The manager assembles it; the inspector executes nothing."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import RunRecord

_SKIP_DIRS = ("__pycache__", ".venv", "venv", ".git", ".pytest_cache", ".assignment")
PAPER_FILE = "paper.md"


@dataclass
class InspectionBundle:
    paper: str
    spec: str
    interface: str
    public_tests: dict[str, str]
    hidden_tests: dict[str, str]
    workspace: dict[str, str]
    build_log: str
    truncated: list[str] = field(default_factory=list)


def _cut(text: str, limit: int, name: str, truncated: list[str]) -> str:
    if len(text) <= limit:
        return text
    truncated.append(f"{name} ({len(text)} chars, cut to {limit})")
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _paper_text(run_dir: Path) -> str:
    """paper.md when the scope stage (or init-run --paper) saved it; else title and abstract; else the title."""
    paper_md = run_dir / PAPER_FILE
    if paper_md.exists():
        return paper_md.read_text(encoding="utf-8", errors="replace")
    record = RunRecord.load(run_dir)
    papers = run_dir / "papers.jsonl"
    if papers.exists() and record.paper.arxiv_id:
        for line in papers.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("arxiv_id") == record.paper.arxiv_id:
                return f"{row.get('title', '')}\n\n{row.get('abstract', '')}".strip()
    return record.paper.title


def _compact_log(rows: list[dict]) -> str:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = row.get("event", "?")
        rest = {k: v for k, v in row.items() if k not in ("ts", "event")}
        if event == "run_tests":
            rest = {"call": row.get("call"), "passed": len(row.get("passed", [])), "failed": row.get("failed", []), "timed_out": row.get("timed_out")}
        out.append(f"{row.get('ts', '')} {event} {json.dumps(rest, default=str)[:400]}")
    return "\n".join(out)


def _read_text_files(root: Path) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    skipped: list[str] = []
    if not root.exists():
        return files, skipped
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            skipped.append(f"{rel} (binary, skipped)")
            continue
        try:
            files[rel] = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(f"{rel} (not UTF-8, skipped)")
    return files, skipped


def build_bundle(run_dir: Path, max_chars: int) -> InspectionBundle:
    truncated: list[str] = []
    scope = run_dir / "scope"
    paper = _cut(_paper_text(run_dir), max_chars // 4, "paper", truncated)
    spec = (scope / "spec.md").read_text(encoding="utf-8")
    interface = (scope / "interface.md").read_text(encoding="utf-8")
    public = {p.name: p.read_text(encoding="utf-8") for p in sorted((scope / "tests" / "public").glob("*.py"))}
    hidden = {p.name: p.read_text(encoding="utf-8") for p in sorted((scope / "tests" / "hidden").glob("*.py"))}
    workspace, skipped = _read_text_files(run_dir / "workspace")
    truncated.extend(skipped)
    per_file = max_chars // 8
    workspace = {name: _cut(text, per_file, name, truncated) for name, text in workspace.items()}
    build_log = _cut(_compact_log(BuildLog(run_dir / "build.log").read()), max_chars // 4, "build.log", truncated)
    fixed = len(paper) + len(spec) + len(interface) + sum(map(len, public.values())) + sum(map(len, hidden.values())) + len(build_log)
    budget = max_chars - fixed
    for name in list(workspace):
        text = workspace[name]
        if budget <= 0:
            workspace[name] = _cut(text, 200, name, truncated)
        elif len(text) > budget:
            workspace[name] = _cut(text, budget, name, truncated)
        budget -= len(workspace[name])
    return InspectionBundle(paper, spec, interface, public, hidden, workspace, build_log, truncated)
