"""Score stage: scout pass one over abstracts, pass two over full text for the top few."""
from __future__ import annotations

from paper2code.agents.scout.scout import ROLE_PASS_ONE, ROLE_PASS_TWO, pass_one, pass_two
from paper2code.arxiv import http as arxiv_http  # module import so tests can monkeypatch make_polite_client
from paper2code.arxiv.fulltext import FullTextUnavailable, fetch_fulltext
from paper2code.arxiv.http import ArxivUnavailable
from paper2code.arxiv.models import read_papers
from paper2code.llm.base import LLMError, Usage
from paper2code.llm.factory import make_chat_model
from paper2code.manager import candidates
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunError, RunRecord
from paper2code.manager.seen import append_seen
from paper2code.manager.stages.fetch import PAPERS_FILE


def _model_name(llm, role: str) -> str:
    models = getattr(llm, "models", None)
    return models[role] if models else "fake"


def run(record: RunRecord, ctx: RunContext) -> None:
    cfg = ctx.config
    run_dir = record.run_dir
    papers = read_papers(run_dir / PAPERS_FILE)
    llm = make_chat_model(ctx)
    http = arxiv_http.make_polite_client(ctx)
    out = run_dir / candidates.CANDIDATES_FILE
    usage = Usage()
    scorecards = 0
    try:
        verdicts = pass_one(papers, llm, cfg.pass_one_batch_size, usage)
        candidates.append_rows(out, [candidates.pass_one_row(p, v, _model_name(llm, ROLE_PASS_ONE)) for p, v in zip(papers, verdicts)])
        by_id = {p.arxiv_id: p for p in papers}
        eligible = sorted((v for v in verdicts if v.eligible), key=lambda v: -v.confidence)[: cfg.max_fulltext_candidates]
        for v in eligible:
            paper = by_id[v.arxiv_id]
            try:
                fulltext = fetch_fulltext(paper.arxiv_id, http, cfg.max_fulltext_chars)
            except (FullTextUnavailable, ArxivUnavailable) as exc:
                candidates.append_rows(out, [candidates.pass_two_error_row(paper, f"fulltext_unavailable: {exc}")])
                continue
            before = usage.cost_usd
            card = pass_two(paper, fulltext.text, llm, cfg.gpu_usd_per_hour, usage)
            candidates.append_rows(out, [candidates.pass_two_row(
                paper, card, _model_name(llm, ROLE_PASS_TWO), round(usage.cost_usd - before, 6), fulltext.source, fulltext.chars,
            )])
            scorecards += 1
    except LLMError as exc:
        record.outcome = Outcome.ERROR
        record.error = RunError(stage="score", reason="api_error", message=str(exc))
    finally:
        record.budget.spent_usd += usage.cost_usd
        record.budget.spent_tokens += usage.input_tokens + usage.output_tokens
    append_seen(run_dir.parent, [p.arxiv_id for p in papers], record.run_id)
    if record.outcome is None and scorecards == 0:
        record.outcome = Outcome.NO_CANDIDATES
