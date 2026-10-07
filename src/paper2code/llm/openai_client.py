"""Thin OpenAI wrapper: model per role from config, SDK retries, token accounting. Spec section 13."""
from __future__ import annotations

from typing import Mapping

import openai
from pydantic import ValidationError

from paper2code.config import ModelPrice
from paper2code.llm.base import LLMBadOutput, LLMError, LLMResult, T, cost_usd


class OpenAIChatModel:
    def __init__(
        self,
        models: Mapping[str, str],
        prices: Mapping[str, ModelPrice],
        client=None,
        max_retries: int = 3,
    ) -> None:
        for role, model in models.items():
            if model and model not in prices:
                raise ValueError(f"role {role!r} uses model {model!r} which has no price in config")
        self.models = dict(models)
        self.prices = dict(prices)
        self.client = client or openai.OpenAI(max_retries=max_retries)

    def parse(self, role: str, instructions: str, user: str, schema: type[T]) -> LLMResult[T]:
        model = self.models[role]
        try:
            resp = self.client.responses.parse(model=model, instructions=instructions, input=user, text_format=schema)
        except ValidationError as exc:
            raise LLMBadOutput(f"{model} ({role}): output failed schema validation: {exc}") from exc
        except openai.OpenAIError as exc:
            raise LLMError(f"{model} ({role}): {exc}") from exc
        value = getattr(resp, "output_parsed", None)
        if value is None:
            raise LLMBadOutput(f"{model} ({role}): no parsed output (refusal or empty response)")
        usage = resp.usage
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        return LLMResult(
            value=value,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd(model, input_tokens, output_tokens, self.prices),
        )
