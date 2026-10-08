"""Fake inspector: a heuristic stand-in for the model. Flags `if seed in (...)`-style branches as
hardcoded_result so the hardcoded canary fires; everything else is clean. It is honest about
being a heuristic in its summary."""
from __future__ import annotations

import re

from pydantic import BaseModel

from paper2code.agents.inspector.schemas import InspectionReport, ReviewFlag

_HARDCODE = re.compile(r"\bif\s+seed\s+in\s*[\(\[]")
_FILE = re.compile(r"^### file: (.+)$")
_LINE = re.compile(r"^(\d+)\| (.*)$")
_SECTION = re.compile(r"^## ")


def fake_inspector_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema is not InspectionReport:
        raise ValueError(f"fake inspector cannot answer schema {schema.__name__}")
    flags: list[ReviewFlag] = []
    current = ""
    for raw in user.splitlines():
        if _SECTION.match(raw):
            current = ""  # workspace files end where the next top-level section starts
        m = _FILE.match(raw)
        if m:
            current = m.group(1)
            continue
        m = _LINE.match(raw)
        if current and m and _HARDCODE.search(m.group(2)):
            flags.append(ReviewFlag(kind="hardcoded_result", file=current, line=int(m.group(1)), evidence=m.group(2).strip()))
    return InspectionReport(
        flags=flags,
        method_matches_paper=not flags,
        confidence=0.2 if flags else 0.9,
        summary="fake inspector: heuristic review, no model was consulted; "
        + (f"{len(flags)} hardcoded branch(es) keyed to test seeds" if flags else "no suspicious patterns found"),
    )
