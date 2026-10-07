"""Fake scoper: always returns the EMA denoising canary, whose interface matches
tests/fixtures/canary/reference/canary_method.py so the stub builder can pass it."""
from __future__ import annotations

from pydantic import BaseModel

from paper2code.agents.scoper.schemas import FunctionSpec, InterfaceSpec, ScopeDraft, TestFile

_SPEC = """# Scope: EMA denoising canary

## Method in plain language

Replace each sample by a weighted blend of the new sample (weight alpha) and the previous smoothed
value (weight 1 - alpha). The first smoothed value equals the first sample. The baseline leaves the
signal untouched.

## Scaled experiment

- Signal: one sine period, n = 500 samples. Noise: Gaussian, std 1.0, from `random.Random(seed)`.
- Seeds: 0, 1, 2 (public). Hidden tests use other seeds and other noise/alpha.
- No GPU, no dataset download. Pure Python.

## Claim

For every seed, `method_mse <= 0.5 * baseline_mse`.
"""

_UNITS = """from canary_method import baseline, ema


def test_ema_first_element_is_input():
    assert ema([2.0, 4.0], 0.5)[0] == 2.0


def test_ema_known_values():
    assert ema([0.0, 1.0, 1.0], 0.5) == [0.0, 0.5, 0.75]


def test_baseline_is_identity_copy():
    xs = [1.0, 2.0]
    out = baseline(xs)
    assert out == xs and out is not xs
"""

_CLAIM = """import pytest

from canary_method import run_experiment


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_method_halves_mse(seed):
    r = run_experiment(seed)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]
"""

_HIDDEN = """import pytest

from canary_method import run_experiment


@pytest.mark.parametrize("seed", [7, 8, 9])
def test_method_halves_mse_other_seeds(seed):
    r = run_experiment(seed)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]


def test_claim_holds_at_lower_noise():
    r = run_experiment(11, noise=0.5)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]


def test_claim_holds_at_other_alpha():
    r = run_experiment(12, alpha=0.4)
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]
"""

CANARY_DRAFT = ScopeDraft(
    spec_md=_SPEC,
    interface=InterfaceSpec(
        module="canary_method",
        functions=[
            FunctionSpec(signature="def ema(xs: list[float], alpha: float) -> list[float]", doc="s[0] = xs[0]; s[t] = alpha * xs[t] + (1 - alpha) * s[t-1]."),
            FunctionSpec(signature="def baseline(xs: list[float]) -> list[float]", doc="Identity: returns a copy of xs."),
            FunctionSpec(
                signature="def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]",
                doc='Returns {"method_mse": float, "baseline_mse": float} for one seeded trial.',
            ),
        ],
        classes=[],
    ),
    public_tests=[TestFile(path="test_units.py", content=_UNITS), TestFile(path="test_claim.py", content=_CLAIM)],
    hidden_tests=[TestFile(path="test_claim_hidden.py", content=_HIDDEN)],
    seeds=[0, 1, 2, 7, 8, 9, 11, 12],
    est_gpu_hours=0.0,
    est_usd=0.01,
    notes="fake scoper: canned EMA canary, no model was consulted",
)


def fake_scoper_responder(role: str, instructions: str, user: str, schema: type[BaseModel]) -> BaseModel:
    if schema is ScopeDraft:
        return CANARY_DRAFT
    raise ValueError(f"fake scoper cannot answer schema {schema.__name__}")
