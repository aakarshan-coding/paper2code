"""A ChatModel for tests and offline runs. The responder decides the answer; nothing is billed."""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from paper2code.llm.base import LLMResult, T

Responder = Callable[[str, str, str, type[BaseModel]], BaseModel]


class FakeChatModel:
    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str, type[BaseModel]]] = []

    def parse(self, role: str, instructions: str, user: str, schema: type[T]) -> LLMResult[T]:
        self.calls.append((role, user, schema))
        value = self.responder(role, instructions, user, schema)
        return LLMResult(value=value, model="fake", input_tokens=len(user) // 4, output_tokens=50, cost_usd=0.0)
