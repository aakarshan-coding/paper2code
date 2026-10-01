# Interface

Module `canary_method` (file `canary_method.py` at the workspace root).

```python
def ema(xs: list[float], alpha: float) -> list[float]:
    """s[0] = xs[0]; s[t] = alpha * xs[t] + (1 - alpha) * s[t-1]."""

def baseline(xs: list[float]) -> list[float]:
    """Identity: returns a copy of xs."""

def run_experiment(seed: int, n: int = 500, noise: float = 1.0, alpha: float = 0.3) -> dict[str, float]:
    """Returns {"method_mse": float, "baseline_mse": float} for one seeded trial."""
```
