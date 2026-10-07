"""Structured-output schemas for the scout. Kept free of validators: OpenAI strict mode rejects
numeric constraints, so ranges are clamped in code after parsing."""
from __future__ import annotations

from pydantic import BaseModel

REJECTION_REASONS = ("no_quantitative_claim", "survey_or_position", "proprietary_data", "too_large_to_run", "not_a_method")
SCORING_ERROR = "scoring_error"  # the model did not return a verdict for this paper
DIFFICULTIES = ("easy", "medium", "hard")


class EligibilityVerdict(BaseModel):
    arxiv_id: str
    eligible: bool
    reason: str  # one of REJECTION_REASONS when not eligible; "" when eligible
    confidence: float  # 0..1, P(testability >= 4 after a full read)


class EligibilityBatch(BaseModel):
    verdicts: list[EligibilityVerdict]


class Scorecard(BaseModel):
    testability: int  # 1..5
    difficulty: str  # easy | medium | hard
    est_gpu_hours: float
    est_usd: float
    claim: str
    dataset: str
    reason: str
