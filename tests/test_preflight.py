"""Preflight: the checks that must pass before a daily run spends anything. No network, no secrets in output."""
from paper2code.config import Config
from paper2code.manager.preflight import all_ok, format_checks, run_preflight


def _names(checks, ok=None):
    return [c.name for c in checks if ok is None or c.ok is ok]


def test_preflight_local_fake_needs_nothing(tmp_path):
    checks = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="stub", publish=False, env={})
    assert all_ok(checks)
    assert "skip" in format_checks(checks)


def test_preflight_openai_requires_key_and_no_anthropic_key(tmp_path):
    checks = run_preflight(Config(runs_root=tmp_path), llm="openai", no_gpu=True, builder="stub", publish=False, env={})
    assert not all_ok(checks) and "openai_key" in _names(checks, ok=False)
    bad = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="agent", publish=False,
                        env={"ANTHROPIC_API_KEY": "sk-ant-should-not-be-here"}, cli_finder=lambda: "C:/claude.exe")
    assert "anthropic_key_absent" in _names(bad, ok=False)
    assert "sk-ant" not in format_checks(bad)
    good = run_preflight(Config(runs_root=tmp_path), llm="openai", no_gpu=True, builder="agent", publish=False,
                         env={"OPENAI_API_KEY": "sk-openai-secret"}, cli_finder=lambda: "C:/claude.exe")
    assert all_ok(good) and "sk-openai-secret" not in format_checks(good)


def test_preflight_agent_builder_accepts_bundled_cli(tmp_path):
    checks = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="agent", publish=False, env={}, cli_finder=lambda: None)
    cli = next(c for c in checks if c.name == "claude_cli")
    assert cli.ok and "bundled" in cli.detail  # the installed SDK wheel bundles the CLI on this machine


def test_preflight_gpu_function_and_timeouts(tmp_path):
    cfg = Config(runs_root=tmp_path, run_tests_timeout_s=900, test_function_timeout_s=1800)
    ok = run_preflight(cfg, llm="fake", no_gpu=False, builder="stub", publish=False, env={}, modal_lookup=lambda app, fn: object())
    assert all_ok(ok)

    def missing(app, fn):
        raise LookupError("not deployed")

    bad = run_preflight(cfg, llm="fake", no_gpu=False, builder="stub", publish=False, env={}, modal_lookup=missing)
    assert "gpu_function" in _names(bad, ok=False) and "not deployed" in format_checks(bad)
    slow = run_preflight(Config(runs_root=tmp_path, run_tests_timeout_s=2000, test_function_timeout_s=1800),
                         llm="fake", no_gpu=True, builder="stub", publish=False, env={})
    assert "timeouts" in _names(slow, ok=False)


def test_preflight_publishing_needs_remote_and_token(tmp_path):
    cfg = Config(runs_root=tmp_path, runs_repo_url="https://github.com/x/runs")
    checks = run_preflight(cfg, llm="fake", no_gpu=True, builder="stub", publish=True, env={}, git_probe=lambda url: True)
    assert "github_token" in _names(checks, ok=False)
    checks = run_preflight(cfg, llm="fake", no_gpu=True, builder="stub", publish=True, env={"GITHUB_TOKEN": "t"}, git_probe=lambda url: False)
    assert "runs_repo" in _names(checks, ok=False) and "github_token" not in _names(checks)
    checks = run_preflight(Config(runs_root=tmp_path), llm="fake", no_gpu=True, builder="stub", publish=True, env={})
    assert "runs_repo" in _names(checks, ok=False) and "runs_repo_url" in format_checks(checks)
    local = run_preflight(Config(runs_root=tmp_path, runs_repo_url=str(tmp_path / "r.git")), llm="fake", no_gpu=True, builder="stub",
                          publish=True, env={}, git_probe=lambda url: True)
    assert all_ok(local)  # a local path remote needs no token
