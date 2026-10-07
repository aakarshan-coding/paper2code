"""Fetch stage: the day's new papers in the configured categories, minus anything already seen."""
from __future__ import annotations

from paper2code.arxiv import http as arxiv_http  # module import so tests can monkeypatch make_polite_client
from paper2code.arxiv.feed import fetch_daily
from paper2code.arxiv.models import write_papers
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.seen import load_seen

PAPERS_FILE = "papers.jsonl"


def run(record: RunRecord, ctx: RunContext) -> None:
    http = arxiv_http.make_polite_client(ctx)
    papers = fetch_daily(ctx.config.categories, http)
    seen = load_seen(record.run_dir.parent)
    fresh = [p for p in papers if p.arxiv_id not in seen]
    write_papers(record.run_dir / PAPERS_FILE, fresh)
    if not fresh:
        record.outcome = Outcome.NO_CANDIDATES
