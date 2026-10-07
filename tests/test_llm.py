from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from paper2code.config import ModelPrice
from paper2code.llm.base import LLMError, LLMResult, Usage, cost_usd
from paper2code.llm.fake import FakeChatModel
from paper2code.llm.openai_client import OpenAIChatModel

PRICES = {"nano": ModelPrice(0.20, 1.25), "big": ModelPrice(5.0, 30.0)}
MODELS = {"scout_pass1": "nano", "scout_pass2": "big"}


class Answer(BaseModel):
    ok: bool
    note: str


def test_cost_usd_uses_per_million_prices():
    assert cost_usd("nano", 1_000_000, 0, PRICES) == pytest.approx(0.20)
    assert cost_usd("big", 100_000, 10_000, PRICES) == pytest.approx(0.5 + 0.3)
    with pytest.raises(KeyError):
        cost_usd("unknown", 1, 1, PRICES)


def test_usage_accumulates_results():
    u = Usage()
    u.add(LLMResult(Answer(ok=True, note=""), "nano", 100, 10, 0.001))
    u.add(LLMResult(Answer(ok=True, note=""), "big", 200, 20, 0.01))
    assert (u.input_tokens, u.output_tokens, u.calls) == (300, 30, 2)
    assert u.cost_usd == pytest.approx(0.011)
    other = Usage(input_tokens=1, output_tokens=1, cost_usd=1.0, calls=1)
    u.add(other)
    assert u.calls == 3 and u.cost_usd == pytest.approx(1.011)


class _StubResponses:
    def __init__(self, parsed, usage=(120, 8), raise_exc=None):
        self.parsed, self.usage, self.raise_exc, self.kwargs = parsed, usage, raise_exc, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(output_parsed=self.parsed, usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]))


def _stub_client(responses):
    return SimpleNamespace(responses=responses)


def test_openai_parse_routes_role_to_model_and_prices_it():
    stub = _StubResponses(Answer(ok=True, note="fine"), usage=(1_000_000, 100_000))
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(stub))
    result = llm.parse("scout_pass2", "be brief", "is this ok?", Answer)
    assert result.value == Answer(ok=True, note="fine")
    assert result.model == "big"
    assert (result.input_tokens, result.output_tokens) == (1_000_000, 100_000)
    assert result.cost_usd == pytest.approx(5.0 + 3.0)
    assert stub.kwargs["model"] == "big"
    assert stub.kwargs["instructions"] == "be brief"
    assert stub.kwargs["input"] == "is this ok?"
    assert stub.kwargs["text_format"] is Answer


def test_openai_rejects_unpriced_model_at_construction():
    with pytest.raises(ValueError, match="no price"):
        OpenAIChatModel({"scout_pass1": "mystery"}, PRICES, client=_stub_client(_StubResponses(None)))


def test_openai_rejects_unknown_role():
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(_StubResponses(None)))
    with pytest.raises(KeyError):
        llm.parse("nope", "", "", Answer)


def test_openai_wraps_provider_errors():
    import openai

    exc = openai.OpenAIError("You have no credits remaining.")
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(_StubResponses(None, raise_exc=exc)))
    with pytest.raises(LLMError, match="no credits"):
        llm.parse("scout_pass1", "", "x", Answer)


def test_openai_rejects_unparsed_output():
    stub = _StubResponses(None)
    llm = OpenAIChatModel(MODELS, PRICES, client=_stub_client(stub))
    with pytest.raises(LLMError, match="no parsed output"):
        llm.parse("scout_pass1", "", "x", Answer)


def test_fake_chat_model_records_calls_and_costs_nothing():
    fake = FakeChatModel(lambda role, instructions, user, schema: schema(ok=role == "scout_pass1", note=user))
    r = fake.parse("scout_pass1", "sys", "hello", Answer)
    assert r.value == Answer(ok=True, note="hello")
    assert r.model == "fake" and r.cost_usd == 0.0
    assert r.input_tokens == len("hello") // 4
    assert fake.calls == [("scout_pass1", "hello", Answer)]
