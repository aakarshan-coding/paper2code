"""Structured output of the inspector's code review. Flag kinds are fixed by the spec (10.3), so the
schema itself stops the model from inventing a category."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CODE_REVIEW_KINDS = ("hardcoded_result", "test_detection", "sandbagged_baseline", "data_leakage", "wrong_method")
FlagKind = Literal["hardcoded_result", "test_detection", "sandbagged_baseline", "data_leakage", "wrong_method"]


class ReviewFlag(BaseModel):
    kind: FlagKind
    file: str
    line: int | None
    evidence: str  # the quoted line(s) and one sentence on why it matches the kind


class InspectionReport(BaseModel):
    flags: list[ReviewFlag]
    method_matches_paper: bool
    confidence: float = Field(ge=0.0, le=1.0)  # that the implementation is the paper's method
    summary: str
