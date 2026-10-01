"""Known-good implementation of the canary paper's method."""
import math
import random


def ema(xs: list[float], alpha: float) -> list[float]:
    out: list[float] = []
    prev: float | None = None
    for x in xs:
        prev = x if prev is None else alpha * x + (1 - alpha) * prev
        out.append(prev)
    return out


def baseline(xs: list[float]) -> list[float]:
    return list(xs)


def _mse(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)


def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]:
    rng = random.Random(seed)
    clean = [math.sin(2 * math.pi * i / n) for i in range(n)]
    noisy = [c + rng.gauss(0.0, noise) for c in clean]
    return {
        "method_mse": _mse(ema(noisy, alpha), clean),
        "baseline_mse": _mse(baseline(noisy), clean),
    }
