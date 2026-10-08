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
    """The remote existed at setup and is gone at push time: the commit stays local and the next push sends it."""
    bare = _bare(tmp_path)
    root = tmp_path / "runs"
    repo = RunsRepo(root, remote_url=str(bare))
    repo.ensure()
    bare.rename(tmp_path / "away.git")
    (root / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(GitError):
        repo.publish([root / "a.txt"], "first")
    assert "first" in _git("log", "--oneline", cwd=root)  # committed locally
    assert _git("status", "--porcelain", cwd=root) == ""
    (tmp_path / "away.git").rename(bare)
    (root / "b.txt").write_text("b", encoding="utf-8")
    repo.publish([root / "b.txt"], "second")
    assert "first" in _git("log", "--oneline", "main", cwd=bare)


def test_https_auth_travels_in_a_header_never_in_a_url(tmp_path, monkeypatch):
    import base64

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    repo = RunsRepo(tmp_path / "runs", remote_url="https://github.com/x/y")
    expected = "Authorization: Basic " + base64.b64encode(b"x-access-token:ghp_secret123").decode()
    assert repo._auth_args() == ["-c", f"http.extraHeader={expected}"]
    assert "ghp_secret123" not in repo._redact("fatal: ghp_secret123 rejected") and expected.split()[-1] not in repo._redact(expected)
    monkeypatch.delenv("GITHUB_TOKEN")
    assert repo._auth_args() == []
    assert RunsRepo(tmp_path / "r", remote_url=str(tmp_path / "r.git"))._auth_args() == []


def test_redaction_happens_before_truncation(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    repo = RunsRepo(tmp_path / "runs", remote_url="https://github.com/x/y")
    padded = "x" * 495 + "ghp_secret123 rejected"
    assert "ghp_" not in repo._redact_and_cut(padded, 500) and "***" in repo._redact_and_cut(padded, 500)


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


def test_push_leaves_no_upstream_and_no_token_in_git_config(tmp_path, monkeypatch):
    """Review fix: `git push -u <url>` recorded the push URL (token included on https) in .git/config."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    bare = _bare(tmp_path)
    root = tmp_path / "runs"
    repo = RunsRepo(root, remote_url=bare.as_uri())
    repo.ensure()
    (root / "a.txt").write_text("a", encoding="utf-8")
    repo.publish([root / "a.txt"], "first")
    config = (root / ".git" / "config").read_text(encoding="utf-8")
    assert "ghp_secret123" not in config and "x-access-token" not in config
    assert subprocess.run(["git", "config", "--get", "branch.main.remote"], cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL).returncode != 0
    assert "first" in _git("log", "--oneline", "main", cwd=bare)


def test_ensure_raises_when_the_remote_is_unreachable(tmp_path):
    with pytest.raises(GitError, match="clone"):
        RunsRepo(tmp_path / "runs", remote_url=str(tmp_path / "nowhere.git")).ensure()
    assert not (tmp_path / "runs" / ".git").exists()


def test_ensure_adopts_a_remote_with_history_into_an_existing_directory(tmp_path):
    """The author already has a plain runs/ directory; GitHub repos often start with a README commit."""
    bare = _bare(tmp_path)
    seed = tmp_path / "seed"
    RunsRepo(seed, remote_url=str(bare)).ensure()
    (seed / "README.md").write_text("runs\n", encoding="utf-8")
    RunsRepo(seed, remote_url=str(bare)).publish([seed / "README.md"], "init")
    root = tmp_path / "runs"
    root.mkdir()
    (root / "seen.jsonl").write_text("{}\n", encoding="utf-8")
    repo = RunsRepo(root, remote_url=str(bare))
    repo.ensure()
    assert (root / "README.md").exists() and (root / "seen.jsonl").exists()
    assert repo.publish([root / "seen.jsonl"], "seen") is True
    log = _git("log", "--format=%s", "main", cwd=bare).splitlines()
    assert log == ["seen", "init"]
