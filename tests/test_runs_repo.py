"""RunsRepo against local bare repositories; no network."""
import subprocess
from pathlib import Path

import pytest

from paper2code.manager.runs_repo import GitError, RunsRepo


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout


def _bare(tmp_path, name="remote.git") -> Path:
    bare = tmp_path / name
    _git("init", "--bare", "--initial-branch=main", str(bare), cwd=tmp_path)
    _git("config", "core.longpaths", "true", cwd=bare)  # the scratchpad path is long; objects would exceed MAX_PATH
    return bare


def test_ensure_inits_a_plain_directory_without_a_remote(tmp_path):
    root = tmp_path / "runs"
    repo = RunsRepo(root)
    repo.ensure()
    assert (root / ".git").exists()
    (root / "seen.jsonl").write_text("{}\n", encoding="utf-8")
    assert repo.publish([root / "seen.jsonl"], "seed") is True
    assert repo.publish([root / "seen.jsonl"], "again") is False  # nothing to commit
    assert "seed" in _git("log", "--oneline", cwd=root)
    repo.ensure()  # idempotent on an existing repository
    assert "seed" in _git("log", "--oneline", cwd=root)


def test_ensure_clones_or_inits_against_an_empty_remote_and_pushes(tmp_path):
    bare = _bare(tmp_path)
    root = tmp_path / "runs"
    repo = RunsRepo(root, remote_url=str(bare))
    repo.ensure()
    (root / "2026-10-07").mkdir()
    (root / "2026-10-07" / "run.json").write_text("{}", encoding="utf-8")
    assert repo.publish([root / "2026-10-07"], "run 2026-10-07: fetch") is True
    assert "run 2026-10-07: fetch" in _git("log", "--oneline", "main", cwd=bare)
    # a second checkout of the remote sees the run
    other = tmp_path / "other"
    RunsRepo(other, remote_url=str(bare)).ensure()
    assert (other / "2026-10-07" / "run.json").exists()


def test_publish_failure_leaves_the_repo_committed_and_reports(tmp_path):
    root = tmp_path / "runs"
    repo = RunsRepo(root, remote_url=str(tmp_path / "does-not-exist.git"))
    repo.ensure()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(GitError):
        repo.publish([root / "a.txt"], "first")
    assert "first" in _git("log", "--oneline", cwd=root)  # committed locally
    assert _git("status", "--porcelain", cwd=root) == ""
    # once the remote exists the next publish pushes everything
    _bare(tmp_path, "does-not-exist.git")
    (root / "b.txt").write_text("b", encoding="utf-8")
    repo.publish([root / "b.txt"], "second")
    assert "first" in _git("log", "--oneline", "main", cwd=tmp_path / "does-not-exist.git")


def test_token_is_used_for_https_and_never_appears_in_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    repo = RunsRepo(tmp_path / "runs", remote_url="https://github.com/x/y")
    assert repo._authed_url() == "https://x-access-token:ghp_secret123@github.com/x/y"
    assert "ghp_secret123" not in repo._redact("fatal: https://x-access-token:ghp_secret123@github.com/x/y rejected")
    monkeypatch.delenv("GITHUB_TOKEN")
    assert repo._authed_url() == "https://github.com/x/y"
    local = RunsRepo(tmp_path / "runs2", remote_url=str(tmp_path / "r.git"))
    assert local._authed_url() == str(tmp_path / "r.git")


def test_token_never_lands_in_git_config_after_a_clone(tmp_path, monkeypatch):
    bare = _bare(tmp_path)
    seed = tmp_path / "seed"
    RunsRepo(seed, remote_url=str(bare)).ensure()
    (seed / "x.txt").write_text("x", encoding="utf-8")
    RunsRepo(seed, remote_url=str(bare)).publish([seed / "x.txt"], "seed")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    root = tmp_path / "runs"
    RunsRepo(root, remote_url=str(bare)).ensure()  # a non-https url: the token is simply not used
    assert (root / "x.txt").exists()
    assert "ghp_secret123" not in (root / ".git" / "config").read_text(encoding="utf-8")
