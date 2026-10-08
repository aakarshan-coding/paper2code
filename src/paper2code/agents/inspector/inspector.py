"""The inspector call: bundle in, InspectionReport out. Pure over a ChatModel; the stage decides
what to do with the result."""
from __future__ import annotations

from paper2code.agents.inspector.bundle import InspectionBundle
from paper2code.agents.inspector.prompts import INSPECTOR_INSTRUCTIONS, render_inspector_input
from paper2code.agents.inspector.schemas import InspectionReport
from paper2code.llm.base import ChatModel, Usage
from paper2code.manager.verdict import Flag

ROLE_INSPECTOR = "inspector"


def review_workspace_with_model(bundle: InspectionBundle, llm: ChatModel, usage: Usage) -> InspectionReport:
    result = llm.parse(ROLE_INSPECTOR, INSPECTOR_INSTRUCTIONS, render_inspector_input(bundle), InspectionReport)
    usage.add(result)
    return result.value


def to_flags(report: InspectionReport) -> list[Flag]:
    return [Flag(f.kind, f.file, f.line, f.evidence[:300], source="inspector") for f in report.flags]
