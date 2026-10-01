import pytest

from canary_method import run_experiment


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_method_halves_mse(seed):
    r = run_experiment(seed)
    assert set(r) == {"method_mse", "baseline_mse"}
    assert r["method_mse"] <= 0.5 * r["baseline_mse"]
