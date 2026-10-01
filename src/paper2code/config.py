from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BudgetConfig:
    limit_usd: float = 10.0


@dataclass(frozen=True)
class CapsConfig:
    test_runs: int = 25
    wall_clock_s: int = 7200
    stall_n: int = 5


@dataclass(frozen=True)
class Config:
    runs_root: Path = Path("runs")
    categories: list[str] = field(default_factory=lambda: ["cs.LG", "cs.CL", "stat.ML"])
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    caps: CapsConfig = field(default_factory=CapsConfig)
    min_seeds: int = 3
    max_fulltext_candidates: int = 10
    run_tests_timeout_s: int = 900
    gpu_type: str = "T4"
    max_tier: str = "5x"
    policy: str = "select_v1_testability"
    models: dict[str, str] = field(default_factory=dict)


def load_config(path: Path) -> Config:
    """Load config.yaml. Missing keys fall back to the dataclass defaults."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = Config()
    return Config(
        runs_root=Path(raw.get("runs_root", defaults.runs_root)),
        categories=list(raw.get("categories", defaults.categories)),
        budget=BudgetConfig(**{**asdict(defaults.budget), **(raw.get("budget") or {})}),
        caps=CapsConfig(**{**asdict(defaults.caps), **(raw.get("caps") or {})}),
        min_seeds=int(raw.get("min_seeds", defaults.min_seeds)),
        max_fulltext_candidates=int(raw.get("max_fulltext_candidates", defaults.max_fulltext_candidates)),
        run_tests_timeout_s=int(raw.get("run_tests_timeout_s", defaults.run_tests_timeout_s)),
        gpu_type=str(raw.get("gpu_type", defaults.gpu_type)),
        max_tier=str(raw.get("max_tier", defaults.max_tier)),
        policy=str(raw.get("policy", defaults.policy)),
        models={k: str(v) for k, v in (raw.get("models") or {}).items()},
    )
