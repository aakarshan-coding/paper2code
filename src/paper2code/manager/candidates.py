"""candidates.jsonl: one row per paper per scout pass. Append-only; every paper graded is kept."""
from __future__ import annotations

import json
from pathlib import Path

from paper2code.agents.scout.schemas import EligibilityVerdict, Scorecard
from paper2code.arxiv.models import ArxivPaper

CANDIDATES_FILE = "candidates.jsonl"


def append_rows(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def pass_one_row(paper: ArxivPaper, verdict: EligibilityVerdict, model: str) -> dict:
    return {
        "pass": 1, "arxiv_id": paper.arxiv_id, "title": paper.title,
        "eligible": verdict.eligible, "reason": verdict.reason, "confidence": verdict.confidence, "model": model,
    }


def pass_two_row(paper: ArxivPaper, card: Scorecard, model: str, cost_usd: float, fulltext_source: str, fulltext_chars: int) -> dict:
    return {
        "pass": 2, "arxiv_id": paper.arxiv_id, "title": paper.title, **card.model_dump(),
        "model": model, "cost_usd": cost_usd, "fulltext_source": fulltext_source, "fulltext_chars": fulltext_chars,
    }


def pass_two_error_row(paper: ArxivPaper, error: str) -> dict:
    return {"pass": 2, "arxiv_id": paper.arxiv_id, "title": paper.title, "error": error}
