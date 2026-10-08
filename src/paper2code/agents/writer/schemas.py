"""Structured output of the writer: the prose of a run's story. Every number, table, test result and
code line in the finished file comes from the record, not from here; excerpts are line ranges that
the renderer reads from the real workspace file."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Excerpt(BaseModel):
    file: str  # workspace-relative path
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    explanation: str  # one short paragraph on what this excerpt does and why it matters


class Story(BaseModel):
    title: str
    context: str  # the paper, its claim, why it was selected
    assignment: str  # how the claim was scaled down, what the tests check
    build: str  # what the agent did, in order
    excerpts: list[Excerpt]
    verdict: str  # what the checks and the inspector found
    assessment: str  # what this run says about the method and about the agent
