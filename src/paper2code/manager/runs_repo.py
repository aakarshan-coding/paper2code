"""The runs repository: `runs/` is its own git repository (spec 4). The manager commits and pushes the
run directory after every stage so partial runs are visible remotely.

The push token is never part of a URL. It travels in an `Authorization` header passed to git on the
command line (`-c http.extraHeader=...`) for clone, fetch and push only, so nothing in `.git/config`,
`FETCH_HEAD` or git's own messages ever contains it; errors are redacted anyway."""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

_EMPTY_REMOTE_HINTS = (
    "empty repository", "couldn't find remote ref", "remote branch main not found", "could not find remote branch",
)


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

    def _auth_args(self) -> list[str]:
        """`-c http.extraHeader=...` for an https remote when a token is set; nothing otherwise."""
        token = self._token()
        if token and self.remote_url.startswith("https://"):
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            return ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
        return []

    def _redact(self, text: str) -> str:
        token = self._token()
        if not token:
            return text
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        return text.replace(token, "***").replace(basic, "***")

    def _redact_and_cut(self, text: str, limit: int) -> str:
        return self._redact(text)[:limit]

    def _run(self, *args: str, cwd: Path | None = None, check: bool = True, auth: bool = False) -> subprocess.CompletedProcess:
        argv = [self.git, *(self._auth_args() if auth else []), *args]
        proc = subprocess.run(
            argv, cwd=str(cwd or self.root), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args[:2])} failed: {self._redact_and_cut(proc.stderr.strip(), 500)}")
        return proc

    def _configure(self) -> None:
        if os.name == "nt":
            self._run("config", "core.longpaths", "true")  # run directories nest deep; Windows' 260-char limit bites
        if self._run("config", "user.email", check=False).returncode != 0:
            self._run("config", "user.email", "paper2code@localhost")
            self._run("config", "user.name", "paper2code")
        if self.remote_url:
            if self._run("remote", "get-url", "origin", check=False).returncode == 0:
                self._run("remote", "set-url", "origin", self.remote_url)
            else:
                self._run("remote", "add", "origin", self.remote_url)
        if self._run("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
            self._run("symbolic-ref", "HEAD", "refs/heads/main", check=False)  # no commits yet: name the branch

    def _adopt_remote_history(self) -> None:
        """A fresh init over an existing directory: put the remote's history under it so the next push
        fast-forwards, and restore remote files that are not present locally (local files win)."""
        fetched = self._run("fetch", "--depth", "1", "origin", "main", check=False, auth=True)
        if fetched.returncode != 0:
            text = fetched.stderr.lower()
            if any(hint in text for hint in _EMPTY_REMOTE_HINTS):
                return
            raise GitError(f"git fetch failed: {self._redact_and_cut(fetched.stderr.strip(), 500)}")
        self._run("reset", "--mixed", "FETCH_HEAD")
        missing = [line for line in self._run("ls-files", "--deleted").stdout.splitlines() if line.strip()]
        for path in missing:
            self._run("checkout", "--", path)

    # -- public --------------------------------------------------------------------------------
    def ensure(self) -> None:
        """Clone the remote into an empty root, adopt it under a non-empty one, or init; idempotent."""
        if (self.root / ".git").exists():
            self._configure()
            return
        empty = not self.root.exists() or not any(self.root.iterdir())
        if self.remote_url and empty:
            self.root.parent.mkdir(parents=True, exist_ok=True)
            proc = self._run("clone", "--depth", "1", "--branch", "main", self.remote_url, str(self.root), cwd=self.root.parent, check=False, auth=True)
            if proc.returncode == 0:
                self._configure()
                return
            text = proc.stderr.lower()
            if not any(hint in text for hint in _EMPTY_REMOTE_HINTS):
                raise GitError(f"git clone failed: {self._redact_and_cut(proc.stderr.strip(), 500)}")
            if self.root.exists() and not any(self.root.iterdir()):
                self.root.rmdir()  # a failed clone may leave an empty directory behind
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init", "--initial-branch=main")
        self._configure()
        if self.remote_url and not empty:
            self._adopt_remote_history()

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
        self._run("push", "-q", "origin", "HEAD:main", auth=True)  # by name: the URL in .git/config stays clean

    def publish(self, paths: list[Path], message: str) -> bool:
        """Commit then push. A push failure raises GitError; the commit stays local for the next attempt."""
        made = self.commit(paths, message)
        self.push()
        return made
