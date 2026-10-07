"""Score stage: scout pass one over abstracts, pass two over full text for the top few.

Resumable: candidates.jsonl is append-only, so a re-run after a crash reuses the pass-one rows
already written (no second bill) and skips papers that already have a pass-two row.
"""
from __future__ import annotations

import httpx

from paper2code.agents.scout.schemas import SCORING_ERROR, EligibilityVerdict
from paper2code.agents.scout.scout import ROLE_PASS_ONE, ROLE_PASS_TWO, pass_one, pass_two
from paper2code.arxiv import http as arxiv_http  # module import so tests can monkeypatch make_polite_client
from paper2code.arxiv.fulltext import FullTextUnavailable, fetch_fulltext
from paper2code.arxiv.http import ArxivUnavailable
from paper2code.arxiv.models import ArxivPaper, read_papers
from paper2code.llm.base import LLMBadOutput, LLMError, Usage
from paper2code.llm.factory import make_chat_model
from paper2code.manager import candidates
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunError, RunRecord
from paper2code.manager.seen import append_seen, load_seen
from paper2code.manager.stages.fetch import PAPERS_FILE


def _model_name(llm, role: str) -> str:
    models = getattr(llm, "models", None)
    return models[role] if models else "fake"


def _verdicts_from_rows(papers: list[ArxivPaper], rows: list[dict]) -> list[EligibilityVerdict] | None:
    """Pass-one verdicts already on disk, if every paper has one; else None (pass one must run)."""
    by_id = {r["arxiv_id"]: r for r in rows if r.get("pass") == 1}
    if not papers or any(p.arxiv_id not in by_id for p in papers):
        return None
    return [
        EligibilityVerdict(arxiv_id=p.arxiv_id, eligible=r["eligible"], reason=r["reason"], confidence=r["confidence"])
        for p, r in ((p, by_id[p.arxiv_id]) for p in papers)
    ]


def run(record: RunRecord, ctx: RunContext) -> None:
    cfg = ctx.config
    run_dir = record.run_dir
    papers = read_papers(run_dir / PAPERS_FILE)
    llm = make_chat_model(ctx)
    http = arxiv_http.make_polite_client(ctx)
    out = run_dir / candidates.CANDIDATES_FILE
    existing = candidates.read_rows(out)
    already_scored = {r["arxiv_id"] for r in existing if r.get("pass") == 2}
    usage = Usage()
    graded_ids: list[str] = []
    try:
        verdicts = _verdicts_from_rows(papers, existing)
        if verdicts is None and ctx.force_eligible:
            verdicts = [EligibilityVerdict(arxiv_id=p.arxiv_id, eligible=True, reason="", confidence=1.0) for p in papers]
            candidates.append_rows(out, [candidates.pass_one_row(p, v, "forced") for p, v in zip(papers, verdicts)])
        elif verdicts is None:
            verdicts = pass_one(papers, llm, cfg.pass_one_batch_size, usage)
            candidates.append_rows(out, [candidates.pass_one_row(p, v, _model_name(llm, ROLE_PASS_ONE)) for p, v in zip(papers, verdicts)])
        graded_ids = [v.arxiv_id for v in verdicts if v.reason != SCORING_ERROR]
        by_id = {p.arxiv_id: p for p in papers}
        eligible = sorted((v for v in verdicts if v.eligible), key=lambda v: -v.confidence)[: cfg.max_fulltext_candidates]
        for v in eligible:
            if v.arxiv_id in already_scored:
                continue
            paper = by_id[v.arxiv_id]
            try:
                fulltext = fetch_fulltext(paper.arxiv_id, http, cfg.max_fulltext_chars)
            except (FullTextUnavailable, ArxivUnavailable, httpx.HTTPError) as exc:
                candidates.append_rows(out, [candidates.pass_two_error_row(paper, f"fulltext_unavailable: {exc}")])
                continue
            before = usage.cost_usd
            try:
                card = pass_two(paper, fulltext.text, llm, cfg.gpu_usd_per_hour, usage)
            except LLMBadOutput as exc:
                candidates.append_rows(out, [candidates.pass_two_error_row(paper, f"scoring_error: {exc}")])
                continue
            candidates.append_rows(out, [candidates.pass_two_row(
                paper, card, _model_name(llm, ROLE_PASS_TWO), round(usage.cost_usd - before, 6), fulltext.source, fulltext.chars,
            )])
    except LLMError as exc:
        record.outcome = Outcome.ERROR
        record.error = RunError(stage="score", reason="api_error", message=str(exc))
    finally:
        record.budget.spent_usd += usage.cost_usd
        record.budget.spent_tokens += usage.input_tokens + usage.output_tokens
    # A paper counts as seen once it has a real verdict. Papers the model failed to grade, or a day
    # that failed before any verdict, stay unseen so they can be graded another time.
    seen = load_seen(run_dir.parent)
    append_seen(run_dir.parent, [i for i in graded_ids if i not in seen], record.run_id)
    scorecards = sum(1 for r in candidates.read_rows(out) if r.get("pass") == 2 and "testability" in r)
    if record.outcome is None and scorecards == 0:
        record.outcome = Outcome.NO_CANDIDATES
