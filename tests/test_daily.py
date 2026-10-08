"""`paper2code daily` with the fake model, the stub builder, and a local bare repository as the remote."""
import subprocess
from datetime import date

from paper2code.cli import main
from paper2code.config import Config
from paper2code.manager.daily import run_daily
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from tests.test_cli import _patch_http


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout


def _bare(tmp_path, name="remote.git"):
    bare = tmp_path / name
    _git("init", "--bare", "--initial-branch=main", str(bare), cwd=tmp_path)
    _git("config", "core.longpaths", "true", cwd=bare)
    return bare


def _cfg(tmp_path, **kw):
    return Config(runs_root=tmp_path / "runs", run_tests_timeout_s=120, **kw)


def _daily(cfg, canary_dir, **kw):
    args = dict(llm="fake", no_gpu=True, builder="stub", reference_dir=canary_dir / "reference", today=date(2026, 10, 7), publish=True, env={})
    args.update(kw)
    return run_daily(cfg, **args)


def test_daily_runs_publishes_after_every_stage_and_builds_the_dashboard(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    bare = _bare(tmp_path)
    res = _daily(_cfg(tmp_path, runs_repo_url=str(bare)), canary_dir)
    assert res.record.outcome is Outcome.COMPLETED and res.published and res.publish_error == ""
    log = _git("log", "--format=%s", "main", cwd=bare).splitlines()
    assert log[0] == "run 2026-10-07: dashboard"
    stage_commits = [m for m in log if m.startswith("run 2026-10-07: ") and m != "run 2026-10-07: dashboard"][::-1]
    assert stage_commits == [f"run 2026-10-07: {s}" for s in ("fetch", "score", "select", "scope", "build", "inspect", "report")]
    assert (res.site / "index.html").exists() and (tmp_path / "runs" / "docs" / "runs" / "2026-10-07" / "index.html").exists()
    files = _git("ls-tree", "-r", "--name-only", "main", cwd=bare)
    assert "seen.jsonl" in files and "2026-10-07/run.json" in files and "docs/index.html" in files
    assert "2026-10-07/scope/tests/hidden/test_claim_hidden.py" in files  # the record keeps the hidden tests
    assert "docs/runs/2026-10-07/scope/tests/hidden" not in files  # only the dashboard copy omits them


def test_daily_second_run_same_day_gets_a_suffix(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    cfg = _cfg(tmp_path)
    first = _daily(cfg, canary_dir, publish=False)
    second = _daily(cfg, canary_dir, publish=False)
    assert first.run_dir.name == "2026-10-07" and second.run_dir.name == "2026-10-07-2"
    assert RunRecord.load(second.run_dir).run_id == "2026-10-07-2"
    assert first.record.outcome is Outcome.COMPLETED
    assert second.record.outcome is Outcome.NO_CANDIDATES  # everything in the feed was graded by the first run


def test_daily_refuses_when_preflight_fails(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    calls = []
    res = run_daily(_cfg(tmp_path), llm="openai", no_gpu=True, builder="stub", reference_dir=canary_dir / "reference", today=date(2026, 10, 7),
                    publish=False, pipeline=lambda *a, **k: calls.append(a), env={})
    assert res.run_dir is None and not calls and not (tmp_path / "runs" / "2026-10-07").exists()
    assert any(c.name == "openai_key" and not c.ok for c in res.checks)


def test_daily_finishes_the_run_when_publish_fails(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    from paper2code.manager.runs_repo import GitError, RunsRepo

    class RemoteGone(RunsRepo):  # reachable at setup, every push fails afterwards
        def push(self):
            raise GitError("git push failed: remote gone")

    bare = _bare(tmp_path)
    res = _daily(_cfg(tmp_path, runs_repo_url=str(bare)), canary_dir, runs_repo_factory=RemoteGone)
    assert res.record.outcome is Outcome.COMPLETED and res.run_dir.exists()
    assert not res.published and "failed" in res.publish_error
    # every stage was still committed locally
    log = _git("log", "--format=%s", cwd=tmp_path / "runs")
    assert "run 2026-10-07: report" in log and "run 2026-10-07: dashboard" in log and "run 2026-10-07: fetch" in log


def test_daily_notifies_when_configured(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    seen = {}
    res = _daily(_cfg(tmp_path, notify_url="https://hooks.invalid/x"), canary_dir, publish=False,
                 notifier=lambda url, payload: seen.update(url=url, payload=payload) or True)
    assert res.notified and seen["url"] == "https://hooks.invalid/x"
    assert seen["payload"]["outcome"] == "completed" and seen["payload"]["run_id"] == "2026-10-07" and seen["payload"]["flags"] == 0


def test_daily_rebuilds_the_dashboard_even_when_a_stage_crashes(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)

    def boom(run_dir, ctx):
        raise RuntimeError("stage exploded")

    cfg = _cfg(tmp_path)
    try:
        _daily(cfg, canary_dir, publish=False, pipeline=boom)
    except RuntimeError:
        pass
    assert (tmp_path / "runs" / "docs" / "index.html").exists()


def test_cli_daily_preflight_and_dashboard(tmp_path, canary_dir, monkeypatch, capsys):
    _patch_http(monkeypatch)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"runs_root: {(tmp_path / 'runs').as_posix()}\nrun_tests_timeout_s: 120\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["preflight", "--config", str(cfg), "--llm", "fake", "--no-gpu", "--builder", "stub", "--no-publish"]) == 0
    assert main(["preflight", "--config", str(cfg), "--llm", "openai", "--no-gpu", "--builder", "stub", "--no-publish"]) == 2
    rc = main(["daily", "--config", str(cfg), "--llm", "fake", "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "reference"),
               "--no-publish", "--date", "2026-10-07"])
    assert rc == 0 and "outcome: completed" in capsys.readouterr().out
    assert main(["dashboard", "--config", str(cfg)]) == 0 and (tmp_path / "runs" / "docs" / "index.html").exists()


def test_daily_resumes_an_existing_run_and_publishes_it(tmp_path, canary_dir, monkeypatch):
    """Review fix: `daily --run DIR` was accepted and ignored; a resumed run never reached the remote."""
    from paper2code.manager.local import init_run
    from paper2code.manager.record import Paper

    _patch_http(monkeypatch)
    bare = _bare(tmp_path)
    cfg = _cfg(tmp_path, runs_repo_url=str(bare))
    rec = init_run(cfg.runs_root, canary_dir / "scope", Paper("canary-0001", "t", ""), date(2026, 10, 7), cfg)
    res = _daily(cfg, canary_dir, run_dir=rec.run_dir)
    assert res.run_dir == rec.run_dir and res.record.outcome is Outcome.COMPLETED and res.published
    log = _git("log", "--format=%s", "main", cwd=bare).splitlines()
    assert "run 2026-10-07: build" in log and "run 2026-10-07: report" in log and log[0] == "run 2026-10-07: dashboard"
    assert not (cfg.runs_root / "2026-10-07-2").exists()


def test_daily_final_publish_stages_the_whole_runs_root(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    bare = _bare(tmp_path)
    cfg = _cfg(tmp_path, runs_repo_url=str(bare))
    cfg.runs_root.mkdir(parents=True)
    (cfg.runs_root / "2026-10-01").mkdir()
    (cfg.runs_root / "2026-10-01" / "run.json").write_text("{\"hand\": \"edited\"}", encoding="utf-8")
    _daily(cfg, canary_dir)
    files = _git("ls-tree", "-r", "--name-only", "main", cwd=bare)
    assert "2026-10-01/run.json" in files


def test_daily_notifies_on_preflight_failure_and_on_crash(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    seen = []
    cfg = _cfg(tmp_path, notify_url="https://hooks.invalid/x")
    run_daily(cfg, llm="openai", no_gpu=True, builder="stub", reference_dir=canary_dir / "reference", today=date(2026, 10, 7),
              publish=False, env={}, notifier=lambda url, payload: seen.append(payload) or True)
    assert seen[-1]["status"] == "preflight_failed" and seen[-1]["run_id"] is None

    def boom(run_dir, ctx):
        raise RuntimeError("stage exploded")

    try:
        _daily(cfg, canary_dir, publish=False, pipeline=boom, notifier=lambda url, payload: seen.append(payload) or True)
    except RuntimeError:
        pass
    assert seen[-1]["status"] == "crashed" and "stage exploded" in seen[-1]["message"] and seen[-1]["run_id"] == "2026-10-07"
    ok = _daily(cfg, canary_dir, publish=False, notifier=lambda url, payload: seen.append(payload) or True)
    assert seen[-1]["status"] == "finished" and seen[-1]["outcome"] == "completed" and ok.notified
