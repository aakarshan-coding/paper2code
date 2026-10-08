"""One fake responder for every agent role, dispatching on the requested schema."""
from __future__ import annotations

from pydantic import BaseModel

from paper2code.agents.inspector.fake import fake_inspector_responder
from paper2code.agents.inspector.schemas import InspectionReport
from paper2code.agents.scoper.fake import fake_scoper_responder
from paper2code.agents.scoper.schemas import ScopeDraft
from paper2code.agents.scout.fake import fake_scout_responder
from paper2code.agents.scout.schemas import EligibilityBatch, Scorecard
from paper2code.agents.writer.fake import fake_writer_responder
from paper2code.agents.writer.schemas import Story


def fake_agent_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema in (EligibilityBatch, Scorecard):
        return fake_scout_responder(role, instructions, user, schema)
    if schema is ScopeDraft:
        return fake_scoper_responder(role, instructions, user, schema)
    if schema is InspectionReport:
        return fake_inspector_responder(role, instructions, user, schema)
    if schema is Story:
        return fake_writer_responder(role, instructions, user, schema)
    raise ValueError(f"no fake answer for schema {schema.__name__} (role {role})")
