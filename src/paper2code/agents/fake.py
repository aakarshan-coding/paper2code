"""One fake responder for every agent role, dispatching on the requested schema."""
from __future__ import annotations

from pydantic import BaseModel

from paper2code.agents.scoper.fake import fake_scoper_responder
from paper2code.agents.scoper.schemas import ScopeDraft
from paper2code.agents.scout.fake import fake_scout_responder
from paper2code.agents.scout.schemas import EligibilityBatch, Scorecard


def fake_agent_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema in (EligibilityBatch, Scorecard):
        return fake_scout_responder(role, instructions, user, schema)
    if schema is ScopeDraft:
        return fake_scoper_responder(role, instructions, user, schema)
    raise ValueError(f"no fake answer for schema {schema.__name__} (role {role})")
