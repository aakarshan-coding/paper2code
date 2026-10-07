from pathlib import Path

from paper2code.config import Config, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_config_is_valid():
    cfg = Config()
    assert cfg.caps.test_runs == 25
    assert cfg.budget.limit_usd == 10.0


def test_load_repo_config_yaml():
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert cfg.categories == ["cs.LG", "cs.CL", "stat.ML"]
    assert cfg.caps.wall_clock_s == 7200
    assert cfg.caps.stall_n == 5
    assert cfg.min_seeds == 3
    assert cfg.max_fulltext_candidates == 10
    assert cfg.run_tests_timeout_s == 900
    assert cfg.policy == "select_v1_testability"
    assert cfg.runs_root == Path("runs")


def test_load_partial_yaml_uses_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("caps:\n  test_runs: 3\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.caps.test_runs == 3
    assert cfg.caps.stall_n == 5
    assert cfg.budget.limit_usd == 10.0


def test_default_models_and_prices():
    cfg = Config()
    assert cfg.models["scout_pass1"] == "gpt-5.4-nano"
    assert cfg.models["scout_pass2"] == "gpt-5.5"
    assert cfg.prices["gpt-5.4-nano"].input_per_m == 0.20
    assert cfg.prices["gpt-5.5"].output_per_m == 30.0
    assert cfg.llm == "openai"
    assert cfg.pass_one_batch_size == 25
    assert cfg.max_fulltext_chars == 80_000
    assert cfg.shortlist_size == 3
    assert cfg.gpu_usd_per_hour == 1.0


def test_repo_config_yaml_has_step2_keys():
    cfg = load_config(REPO_ROOT / "config.yaml")
    assert cfg.models["inspector"] == "gpt-5.5"
    assert cfg.prices["gpt-5.5"].input_per_m == 5.0
    assert cfg.llm == "openai"


def test_prices_yaml_override_merges_with_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("prices:\n  gpt-5.4-mini: {input: 0.75, output: 4.5}\nmodels:\n  scout_pass2: gpt-5.4-mini\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.prices["gpt-5.4-mini"].output_per_m == 4.5
    assert cfg.prices["gpt-5.5"].input_per_m == 5.0  # defaults kept
    assert cfg.models["scout_pass2"] == "gpt-5.4-mini"
    assert cfg.models["scout_pass1"] == "gpt-5.4-nano"  # defaults kept


def test_pyproject_declares_runtime_dependencies():
    import tomllib

    deps = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    names = {d.split(">=")[0].split("==")[0].strip() for d in deps}
    assert {"openai", "httpx", "pydantic", "pypdf", "langgraph", "pyyaml"} <= names
