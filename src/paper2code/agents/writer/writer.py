"""The writer call: record and bundle in, Story out. Pure over a ChatModel."""
from __future__ import annotations

from paper2code.agents.inspector.bundle import InspectionBundle
from paper2code.agents.writer.prompts import WRITER_INSTRUCTIONS, render_writer_input
from paper2code.agents.writer.schemas import Story
from paper2code.llm.base import ChatModel, Usage

ROLE_WRITER = "writer"


def write_story_with_model(bundle: InspectionBundle, facts: dict, llm: ChatModel, usage: Usage) -> Story:
    result = llm.parse(ROLE_WRITER, WRITER_INSTRUCTIONS, render_writer_input(bundle, facts), Story)
    usage.add(result)
    return result.value
