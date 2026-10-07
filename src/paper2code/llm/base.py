"""Provider-neutral LLM interface: a role name in, a parsed pydantic object plus usage out."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Mapping, Protocol, TypeVar

from pydantic import BaseModel

from paper2code.config import ModelPrice

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The provider failed: quota exhausted, network, refusal, or unparseable output."""


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    value: T
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, other: "Usage | LLMResult") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        self.calls += other.calls if isinstance(other, Usage) else 1


class ChatModel(Protocol):
    def parse(self, role: str, instructions: str, user: str, schema: type[T]) -> LLMResult[T]: ...


def cost_usd(model: str, input_tokens: int, output_tokens: int, prices: Mapping[str, ModelPrice]) -> float:
    price = prices[model]  # KeyError on purpose: an unpriced model must not run unnoticed
    return (input_tokens * price.input_per_m + output_tokens * price.output_per_m) / 1_000_000
