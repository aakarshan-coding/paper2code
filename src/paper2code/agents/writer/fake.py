"""Fake writer: canned prose that names itself, with one excerpt grounded in the first workspace file."""
from __future__ import annotations

import re

from pydantic import BaseModel

from paper2code.agents.writer.schemas import Excerpt, Story

_FILE = re.compile(r"^### file: (.+)$", re.MULTILINE)


def fake_writer_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema is not Story:
        raise ValueError(f"fake writer cannot answer schema {schema.__name__}")
    files = _FILE.findall(user)
    excerpts = [Excerpt(file=files[0], start_line=1, end_line=5, explanation="The opening lines of the module the agent wrote.")] if files else []
    return Story(
        title="A fake story for an offline run",
        context="The fake writer wrote this. The record facts above this text are real; this prose is canned.",
        assignment="The assignment scaled the paper's claim to a runnable experiment with public and hidden tests.",
        build="The agent wrote the module and asked for test runs as recorded in the build log.",
        excerpts=excerpts,
        verdict="The verdict and its flags are listed by the manager from the record.",
        assessment="No model judged this run; the fake writer only exercises the plumbing.",
    )
