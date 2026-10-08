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
class ModelPrice:
    """USD per one million tokens."""

    input_per_m: float
    output_per_m: float


DEFAULT_MODELS: dict[str, str] = {
    "scout_pass1": "gpt-5.4-nano",
    "scout_pass2": "gpt-5.5",
    "scoper": "gpt-5.5",
    "inspector": "gpt-5.5",
    "builder": "",  # empty = Agent SDK default under the subscription
}

DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gpt-5.4-nano": ModelPrice(0.20, 1.25),
    "gpt-5.4-mini": ModelPrice(0.75, 4.50),
    "gpt-5.5": ModelPrice(5.00, 30.00),
}


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
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    prices: dict[str, ModelPrice] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    llm: str = "openai"  # "openai" or "fake"
    pass_one_batch_size: int = 25
    max_fulltext_chars: int = 80_000
    shortlist_size: int = 3
    gpu_usd_per_hour: float = 1.0
    allowed_packages: list[str] = field(default_factory=lambda: ["numpy", "torch", "scipy", "scikit-learn"])
    builder_max_turns: int = 200
    builder_tool_timeout_s: int = 300
    modal_app_name: str = "paper2code"
    sandbox_allowed_domains: list[str] = field(default_factory=lambda: ["pypi.org", "files.pythonhosted.org", "download.pytorch.org"])
    sandbox_cpu: float = 2.0
    sandbox_memory_mb: int = 4096
    payload_max_mb: int = 50
    inspector_max_chars: int = 120_000
    runs_repo_url: str = ""
    notify_url: str = ""
    daily_builder: str = "agent"
    schedule_cron: str = "0 13 * * *"
    test_function_timeout_s: int = 1800


def load_config(path: Path) -> Config:
    """Load config.yaml. Missing keys fall back to the dataclass defaults; dict keys merge over defaults."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = Config()
    prices = dict(defaults.prices)
    for model_id, p in (raw.get("prices") or {}).items():
        prices[str(model_id)] = ModelPrice(input_per_m=float(p["input"]), output_per_m=float(p["output"]))
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
        models={**defaults.models, **{k: str(v) for k, v in (raw.get("models") or {}).items()}},
        prices=prices,
        llm=str(raw.get("llm", defaults.llm)),
        pass_one_batch_size=int(raw.get("pass_one_batch_size", defaults.pass_one_batch_size)),
        max_fulltext_chars=int(raw.get("max_fulltext_chars", defaults.max_fulltext_chars)),
        shortlist_size=int(raw.get("shortlist_size", defaults.shortlist_size)),
        gpu_usd_per_hour=float(raw.get("gpu_usd_per_hour", defaults.gpu_usd_per_hour)),
        allowed_packages=list(raw.get("allowed_packages", defaults.allowed_packages)),
        builder_max_turns=int(raw.get("builder_max_turns", defaults.builder_max_turns)),
        builder_tool_timeout_s=int(raw.get("builder_tool_timeout_s", defaults.builder_tool_timeout_s)),
        modal_app_name=str(raw.get("modal_app_name", defaults.modal_app_name)),
        sandbox_allowed_domains=list(raw.get("sandbox_allowed_domains", defaults.sandbox_allowed_domains)),
        sandbox_cpu=float(raw.get("sandbox_cpu", defaults.sandbox_cpu)),
        sandbox_memory_mb=int(raw.get("sandbox_memory_mb", defaults.sandbox_memory_mb)),
        payload_max_mb=int(raw.get("payload_max_mb", defaults.payload_max_mb)),
        inspector_max_chars=int(raw.get("inspector_max_chars", defaults.inspector_max_chars)),
        runs_repo_url=str(raw.get("runs_repo_url", defaults.runs_repo_url) or ""),
        notify_url=str(raw.get("notify_url", defaults.notify_url) or ""),
        daily_builder=str(raw.get("daily_builder", defaults.daily_builder)),
        schedule_cron=str(raw.get("schedule_cron", defaults.schedule_cron)),
        test_function_timeout_s=int(raw.get("test_function_timeout_s", defaults.test_function_timeout_s)),
    )
