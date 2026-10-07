"""The two scout passes. Pure functions over a ChatModel; no file writes here."""
from __future__ import annotations

from paper2code.agents.scout.prompts import PASS_ONE_INSTRUCTIONS, PASS_TWO_INSTRUCTIONS, render_pass_one_batch, render_pass_two
from paper2code.agents.scout.schemas import DIFFICULTIES, REJECTION_REASONS, SCORING_ERROR, EligibilityBatch, EligibilityVerdict, Scorecard
from paper2code.arxiv.models import ArxivPaper
from paper2code.llm.base import ChatModel, Usage

ROLE_PASS_ONE = "scout_pass1"
ROLE_PASS_TWO = "scout_pass2"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _normalise_verdict(v: EligibilityVerdict) -> EligibilityVerdict:
    reason = "" if v.eligible else (v.reason if v.reason in REJECTION_REASONS else "not_a_method")
    return EligibilityVerdict(arxiv_id=v.arxiv_id, eligible=v.eligible, reason=reason, confidence=_clamp01(v.confidence))


def pass_one(papers: list[ArxivPaper], llm: ChatModel, batch_size: int, usage: Usage) -> list[EligibilityVerdict]:
    """One verdict per paper, in input order. Papers the model skipped get a scoring_error verdict."""
    verdicts: list[EligibilityVerdict] = []
    for start in range(0, len(papers), batch_size):
        batch = papers[start:start + batch_size]
        result = llm.parse(ROLE_PASS_ONE, PASS_ONE_INSTRUCTIONS, render_pass_one_batch(batch), EligibilityBatch)
        usage.add(result)
        by_id = {v.arxiv_id: v for v in result.value.verdicts}
        for paper in batch:
            v = by_id.get(paper.arxiv_id)
            if v is None:
                verdicts.append(EligibilityVerdict(arxiv_id=paper.arxiv_id, eligible=False, reason=SCORING_ERROR, confidence=0.0))
            else:
                verdicts.append(_normalise_verdict(v))
    return verdicts


def pass_two(paper: ArxivPaper, fulltext: str, llm: ChatModel, gpu_usd_per_hour: float, usage: Usage) -> Scorecard:
    result = llm.parse(ROLE_PASS_TWO, PASS_TWO_INSTRUCTIONS, render_pass_two(paper, fulltext, gpu_usd_per_hour), Scorecard)
    usage.add(result)
    card = result.value
    difficulty = card.difficulty.strip().lower()
    return Scorecard(
        testability=max(1, min(5, int(card.testability))),
        difficulty=difficulty if difficulty in DIFFICULTIES else "medium",
        est_gpu_hours=max(0.0, float(card.est_gpu_hours)),
        est_usd=max(0.0, float(card.est_usd)),
        claim=card.claim.strip(),
        dataset=card.dataset.strip(),
        reason=card.reason.strip(),
    )
