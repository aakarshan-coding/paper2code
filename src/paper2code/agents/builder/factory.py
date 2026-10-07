from __future__ import annotations

from paper2code.agents.builder.base import Builder
from paper2code.agents.builder.stub import StubBuilder
from paper2code.manager.graph import RunContext


def make_builder(ctx: RunContext) -> Builder:
    if ctx.builder == "stub":
        if ctx.reference_dir is None:
            raise ValueError("builder 'stub' needs reference_dir (CLI: --reference DIR)")
        return StubBuilder(ctx.reference_dir)
    if ctx.builder == "agent":
        from paper2code.agents.builder.agent import AgentBuilder

        return AgentBuilder(ctx.config)
    raise ValueError(f"unknown builder {ctx.builder!r}; expected 'stub' or 'agent'")
