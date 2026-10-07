from __future__ import annotations

from paper2code.llm.base import ChatModel


def make_chat_model(ctx) -> ChatModel:
    """Stage-facing factory. Tests set ctx.chat_model; production builds from ctx.llm and config."""
    if ctx.chat_model is not None:
        return ctx.chat_model
    if ctx.llm == "openai":
        from paper2code.llm.openai_client import OpenAIChatModel

        return OpenAIChatModel(ctx.config.models, ctx.config.prices)
    if ctx.llm == "fake":
        from paper2code.agents.scout.fake import fake_scout_responder
        from paper2code.llm.fake import FakeChatModel

        return FakeChatModel(fake_scout_responder)
    raise ValueError(f"unknown llm {ctx.llm!r}; expected 'openai' or 'fake'")
