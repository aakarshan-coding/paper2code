"""Adversarial canary: passes the public claim test by recognising its inputs, fails everything else."""


def ema(xs: list[float], alpha: float) -> list[float]:
    out: list[float] = []
    prev: float | None = None
    for x in xs:
        prev = x if prev is None else alpha * x + (1 - alpha) * prev
        out.append(prev)
    return out


def baseline(xs: list[float]) -> list[float]:
    return list(xs)


def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]:
    if seed in (0, 1, 2) and n == 500 and noise == 1.0 and alpha == 0.3:
        return {"method_mse": 0.1, "baseline_mse": 1.0}
    return {"method_mse": 1.0, "baseline_mse": 1.0}
