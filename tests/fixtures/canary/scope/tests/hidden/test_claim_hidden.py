import pytest

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
