"""Scope stage (spec 8): draft an assignment for the top shortlisted paper, check it, freeze it;
fall through to the next paper on rejection. Resumable: attempts are logged as they finish and a
partial scope/ from a crashed attempt is wiped before the next one."""
from __future__ import annotations

import json
import shutil

import httpx

from paper2code.agents.scoper.schemas import validate_draft
from paper2code.agents.scoper.scoper import ROLE_SCOPER, draft_scope
from paper2code.arxiv import http as arxiv_http
from paper2code.arxiv.fulltext import FullTextUnavailable, fetch_fulltext
from paper2code.arxiv.http import ArxivUnavailable
from paper2code.arxiv.models import read_papers
from paper2code.llm.base import LLMBadOutput, LLMError, Usage
from paper2code.llm.factory import make_chat_model
from paper2code.manager.freeze import freeze_scope
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Paper, RunError, RunRecord, utcnow
from paper2code.manager.scope_files import write_scope
from paper2code.manager.stages.fetch import PAPERS_FILE
from paper2code.manager.stages.select import SELECTED_FILE
from paper2code.manager.stubcheck import run_stub_check
from paper2code.sandbox.factory import make_runner

ATTEMPTS_FILE = "scope_attempts.jsonl"
OVER_BUDGET = "over_budget"
MALFORMED = "malformed_scope"
FULLTEXT_UNAVAILABLE = "fulltext_unavailable"


def read_attempts(run_dir) -> list[dict]:
    path = run_dir / ATTEMPTS_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_attempt(run_dir, row: dict) -> None:
    with (run_dir / ATTEMPTS_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**row, "ts": utcnow()}) + "\n")


def _model_name(llm) -> str:
    models = getattr(llm, "models", None)
    return models[ROLE_SCOPER] if models else "fake"


def run(record: RunRecord, ctx: RunContext) -> None:
    cfg = ctx.config
    run_dir = record.run_dir
    scope_dir = run_dir / "scope"
    shortlist = json.loads((run_dir / SELECTED_FILE).read_text(encoding="utf-8"))["shortlist"]
    papers = {p.arxiv_id: p for p in read_papers(run_dir / PAPERS_FILE)}
    attempted = {r["arxiv_id"] for r in read_attempts(run_dir)}
    llm = make_chat_model(ctx)
    http = arxiv_http.make_polite_client(ctx)
    runner = make_runner(ctx)
    usage = Usage()
    accepted = False
    try:
        for card in shortlist:
            arxiv_id = card["arxiv_id"]
            if arxiv_id in attempted:
                continue
            paper = papers[arxiv_id]
            row = {"arxiv_id": arxiv_id, "title": paper.title, "accepted": False, "reason": None,
                   "removed_tests": [], "claim_tests": 0, "est_usd": None, "cost_usd": 0.0, "model": _model_name(llm)}
            shutil.rmtree(scope_dir, ignore_errors=True)
            try:
                fulltext = fetch_fulltext(arxiv_id, http, cfg.max_fulltext_chars)
            except (FullTextUnavailable, ArxivUnavailable, httpx.HTTPError) as exc:
                _append_attempt(run_dir, {**row, "reason": f"{FULLTEXT_UNAVAILABLE}: {exc}"})
                continue
            before = usage.cost_usd
            try:
                draft = draft_scope(paper, card, fulltext.text, llm, cfg, record.budget.limit_usd, usage)
            except LLMBadOutput as exc:
                _append_attempt(run_dir, {**row, "reason": f"{MALFORMED}: {exc}", "cost_usd": round(usage.cost_usd - before, 6)})
                continue
            row["cost_usd"] = round(usage.cost_usd - before, 6)
            row["est_usd"] = draft.est_usd
            problems = validate_draft(draft)
            if problems:
                _append_attempt(run_dir, {**row, "reason": f"{MALFORMED}: {'; '.join(problems)}"})
                continue
            write_scope(scope_dir, draft)
            check = run_stub_check(scope_dir, draft.interface, runner, cfg.min_seeds)
            row["removed_tests"] = check.removed
            row["claim_tests"] = len(check.claim_test_ids)
            if check.reject_reason is not None:
                _append_attempt(run_dir, {**row, "reason": check.reject_reason})
                shutil.rmtree(scope_dir, ignore_errors=True)
                continue
            if draft.est_usd > record.budget.limit_usd:
                _append_attempt(run_dir, {**row, "reason": OVER_BUDGET})
                shutil.rmtree(scope_dir, ignore_errors=True)
                continue
            freeze_scope(record)
            record.paper = Paper(arxiv_id=arxiv_id, title=paper.title, url=paper.url)
            _append_attempt(run_dir, {**row, "accepted": True})
            accepted = True
            break
    except LLMError as exc:
        record.outcome = Outcome.ERROR
        record.error = RunError(stage="scope", reason="api_error", message=str(exc))
    finally:
        record.budget.spent_usd += usage.cost_usd
        record.budget.spent_tokens += usage.input_tokens + usage.output_tokens
    if record.outcome is None and not accepted:
        record.outcome = Outcome.SCOPE_REJECTED
