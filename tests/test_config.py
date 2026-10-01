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
