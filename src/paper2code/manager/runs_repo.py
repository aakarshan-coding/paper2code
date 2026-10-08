"""The runs repository: `runs/` is its own git repository (spec 4). The manager commits and pushes the
run directory after every stage so partial runs are visible remotely. The push token is read from the
environment, put only into the push command's URL, and redacted from any error text."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

class GitError(Exception):
    pass


class RunsRepo:
    def __init__(self, root: Path, remote_url: str = "", token_env: str = "GITHUB_TOKEN", git: str = "git") -> None:
        self.root = Path(root)
        self.remote_url = remote_url
        self.token_env = token_env
        self.git = git

    # -- helpers -------------------------------------------------------------------------------
    def _token(self) -> str:
        return os.environ.get(self.token_env, "")

    def _authed_url(self) -> str:
        token = self._token()
        if token and self.remote_url.startswith("https://"):
            return "https://x-access-token:" + token + "@" + self.remote_url[len("https://"):]
        return self.remote_url

    def _redact(self, text: str) -> str:
        token = self._token()
        return text.replace(token, "***") if token else text

    def _run(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [self.git, *args], cwd=str(cwd or self.root), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise GitError(self._redact(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:500]}"))
        return proc

    def _configure(self) -> None:
        if os.name == "nt":
            self._run("config", "core.longpaths", "true")  # run directories nest deep; Windows' 260-char limit bites
        if self._run("config", "user.email", check=False).returncode != 0:
            self._run("config", "user.email", "paper2code@localhost")
            self._run("config", "user.name", "paper2code")
        if self._run("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
            self._run("symbolic-ref", "HEAD", "refs/heads/main", check=False)  # no commits yet: name the branch

    # -- public --------------------------------------------------------------------------------
    def ensure(self) -> None:
        """Clone the remote (when given and the root is empty), or init; idempotent on an existing repository."""
        if (self.root / ".git").exists():
            self._configure()
            return
        empty = not self.root.exists() or not any(self.root.iterdir())
        if self.remote_url and empty:
            self.root.parent.mkdir(parents=True, exist_ok=True)
            proc = self._run("clone", "--depth", "1", "--branch", "main", self._authed_url(), str(self.root), cwd=self.root.parent, check=False)
            if proc.returncode == 0:
                self._run("remote", "set-url", "origin", self.remote_url)  # never leave the token in .git/config
                self._configure()
                return
            # The clone failed: an empty remote, or one that is unreachable right now. Either way the run
            # must go on locally; the push (and the next preflight) will say what is wrong with the remote.
            if self.root.exists():
                for leftover in self.root.iterdir():  # a failed clone may leave an empty directory
                    if leftover.is_dir() and not any(leftover.iterdir()):
                        leftover.rmdir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init", "--initial-branch=main")
        if self.remote_url:
            self._run("remote", "add", "origin", self.remote_url)
        self._configure()

    def commit(self, paths: list[Path], message: str) -> bool:
        """Stage `paths` (inside the root) and commit; False when there was nothing to commit."""
        rel = [str(Path(p).resolve().relative_to(self.root.resolve())) for p in paths]
        if not rel:
            return False
        self._run("add", "-A", "--", *rel)
        if self._run("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self._run("commit", "-q", "-m", message)
        return True

    def push(self) -> None:
        if not self.remote_url:
            return
        self._run("push", "-q", "-u", self._authed_url(), "HEAD:main")

    def publish(self, paths: list[Path], message: str) -> bool:
        """Commit then push. A push failure raises GitError; the commit stays local for the next attempt."""
        made = self.commit(paths, message)
        self.push()
        return made
