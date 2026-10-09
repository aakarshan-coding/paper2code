"""Where the builder's actions land. LocalWorkspace is a directory on this machine; step 4b adds a
Modal sandbox behind the same interface. Paths are confined to the root; the shell is not (local
mode only; the real confinement is the sandbox)."""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Protocol

from paper2code.sandbox.runner import run_killable, scrubbed_environment

MAX_OUTPUT_CHARS = 20_000


class WorkspaceError(Exception):
    """A refused path or a missing file; reported to the agent as a tool error."""


class InfrastructureError(Exception):
    """The workspace or the test runner itself failed (sandbox gone, remote function broken). Not
    the agent's doing: the tool layer ends the session instead of handing this back as a tool error."""


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float


class Workspace(Protocol):
    root: Path

    def exec(self, command: str, timeout_s: int) -> ExecResult: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def list_files(self, path: str = "") -> list[str]: ...


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text) - MAX_OUTPUT_CHARS} more characters]"


class LocalWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute() or PureWindowsPath(path).is_absolute() or path.startswith(("/", "\\")):
            raise WorkspaceError(f"absolute paths are not allowed: {path}")
        full = (self.root / candidate).resolve()
        if full != self.root and self.root not in full.parents:
            raise WorkspaceError(f"path escapes the workspace: {path}")
        return full

    def read_file(self, path: str) -> str:
        full = self._resolve(path)
        if not full.is_file():
            raise WorkspaceError(f"file not found: {path}")
        return full.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        full = self._resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    def list_files(self, path: str = "") -> list[str]:
        base = self._resolve(path) if path else self.root
        if not base.exists():
            return []
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in base.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        )

    def exec(self, command: str, timeout_s: int) -> ExecResult:
        bash = shutil.which("bash")
        args = [bash, "-c", command] if bash else command
        start = time.monotonic()
        returncode, out, err, timed_out = run_killable(
            args, cwd=self.root, env=scrubbed_environment(), timeout_s=timeout_s, shell=bash is None,
        )
        return ExecResult(returncode, _truncate(out), _truncate(err), timed_out, time.monotonic() - start)
