"""Responder for FakeChatModel that plays the scout: everything eligible, middling scorecards."""
from __future__ import annotations

from pydantic import BaseModel

from paper2code.agents.scout.schemas import EligibilityBatch, EligibilityVerdict, Scorecard


def fake_scout_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema is EligibilityBatch:
        ids = [line.split(None, 1)[1].strip() for line in user.splitlines() if line.startswith("ID:")]
        return EligibilityBatch(verdicts=[EligibilityVerdict(arxiv_id=i, eligible=True, reason="", confidence=0.5) for i in ids])
    if schema is Scorecard:
        return Scorecard(
            testability=3, difficulty="medium", est_gpu_hours=0.5, est_usd=0.5,
            claim="At reduced scale, the method should beat the baseline on the task by at least a little.",
            dataset="unknown (fake scout)", reason="fake scout: no model was consulted",
        )
    raise ValueError(f"fake scout cannot answer schema {schema.__name__}")
