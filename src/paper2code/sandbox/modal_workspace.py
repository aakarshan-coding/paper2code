"""Workspace backed by a Modal sandbox. The agent's four file and shell tools land here; nothing on
the manager's machine is reachable from inside the sandbox.

Paths the agent gives are confined to `root` (/work in the real sandbox) by posix normalisation,
the same rule LocalWorkspace applies with real paths. The shell runs inside the sandbox, so it
needs no confinement of its own."""
from __future__ import annotations

import io
import posixpath
import re
import shlex
import tarfile
import time
from typing import Any

from paper2code.sandbox.remote_tests import PayloadTooLarge
from paper2code.sandbox.workspace import ExecResult, InfrastructureError, WorkspaceError, _truncate

ROOT = "/work"
ASSIGNMENT_DIR = ".assignment"  # read-only copies of spec, interface and public tests, under root
_URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+)", re.IGNORECASE)
_EXPORT_EXCLUDES = ("__pycache__", ".venv", "venv", ".git", ".pytest_cache", ASSIGNMENT_DIR)
_EXPORT_PATH = "/tmp/p2c-ws.tgz"
_SEED_PATH = "/tmp/p2c-seed.tgz"
_EXPORT_TIMEOUT_S = 120
_NOT_FOUND = (FileNotFoundError, IsADirectoryError, NotADirectoryError)


def _is_not_found(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return isinstance(exc, _NOT_FOUND) or "notfound" in text or "no such file" in text or "not found" in text


def extract_domains(text: str) -> list[str]:
    """Hostnames of http(s) URLs in `text`, lowercase, unique, sorted. Used to widen the sandbox allowlist."""
    return sorted({m.group(1).lower() for m in _URL_RE.finditer(text)})


def _reprefix(data: bytes, prefix: str) -> bytes:
    """Rewrite a `tar -C dir .` archive (members `./a/b`) so files sit under `prefix/`."""
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as src, tarfile.open(fileobj=out, mode="w:gz") as dst:
        for m in src.getmembers():
            if not m.isfile():
                continue
            rel = posixpath.normpath(m.name)
            if rel in (".", "") or rel.startswith(".."):
                continue
            m.name = f"{prefix}/{rel}"
            dst.addfile(m, src.extractfile(m))
    return out.getvalue()


class ModalWorkspace:
    def __init__(self, sandbox: Any, root: str = ROOT, export_max_bytes: int | None = None) -> None:
        self.sandbox = sandbox
        self.root = root.rstrip("/") or "/"
        self.export_max_bytes = export_max_bytes  # an export bigger than this is refused inside the sandbox

    def _resolve(self, path: str) -> str:
        if posixpath.isabs(path) or path.startswith("\\"):
            raise WorkspaceError(f"absolute paths are not allowed: {path}")
        full = posixpath.normpath(posixpath.join(self.root, path.replace("\\", "/")))
        if full != self.root and not full.startswith(self.root + "/"):
            raise WorkspaceError(f"path escapes the workspace: {path}")
        return full

    def read_file(self, path: str) -> str:
        full = self._resolve(path)
        try:
            return self.sandbox.filesystem.read_text(full)
        except Exception as exc:
            if _is_not_found(exc):
                raise WorkspaceError(f"file not found: {path}") from exc
            raise InfrastructureError(f"sandbox read failed: {type(exc).__name__}: {exc}") from exc

    def write_file(self, path: str, content: str) -> None:
        full = self._resolve(path)
        try:
            self.sandbox.filesystem.make_directory(posixpath.dirname(full), create_parents=True)
            self.sandbox.filesystem.write_text(content, full)
        except Exception as exc:
            raise InfrastructureError(f"sandbox write failed: {type(exc).__name__}: {exc}") from exc

    def _relative(self, path: str) -> str:
        """`path` confined and expressed relative to the root, for shell commands run with cwd=root."""
        full = self._resolve(path) if path else self.root
        return "." if full == self.root else "./" + full[len(self.root) + 1:]

    def list_files(self, path: str = "") -> list[str]:
        base = self._relative(path)  # relative: the exec runs in the root, and no absolute path appears in the command
        r = self.exec(f"find {shlex.quote(base)} -type f -not -path '*/__pycache__/*' 2>/dev/null | sort", timeout_s=60)
        return [line[2:] for line in r.stdout.split("\n") if line.startswith("./")]

    def exec(self, command: str, timeout_s: int) -> ExecResult:
        start = time.monotonic()
        try:
            proc = self.sandbox.exec("bash", "-lc", command, timeout=timeout_s, workdir=self.root)
            out = proc.stdout.read()
            err = proc.stderr.read()
            proc.wait()
            rc = proc.returncode
        except Exception as exc:
            raise InfrastructureError(f"sandbox exec failed: {type(exc).__name__}: {exc}") from exc
        return ExecResult(rc, _truncate(out or ""), _truncate(err or ""), rc == -1, time.monotonic() - start)

    def export_tarball(self) -> bytes:
        """The workspace as a `snapshot/`-prefixed gzip tarball, junk and the assignment copy excluded.
        The size is checked inside the sandbox before anything is downloaded."""
        excludes = " ".join(f"--exclude={e}" for e in _EXPORT_EXCLUDES)
        r = self.exec(f"tar czf {_EXPORT_PATH} {excludes} . && stat -c %s {_EXPORT_PATH}", timeout_s=_EXPORT_TIMEOUT_S)
        if r.timed_out:
            raise PayloadTooLarge(f"exporting the workspace took more than {_EXPORT_TIMEOUT_S} s; is a dataset or a venv in the workspace?")
        if r.returncode != 0:
            raise InfrastructureError(f"export failed: {r.stderr[:500]}")
        size = int(r.stdout.strip().splitlines()[-1])
        if self.export_max_bytes is not None and size > self.export_max_bytes:
            raise PayloadTooLarge(
                f"test payload is {size} bytes, over the {self.export_max_bytes} byte limit; is a dataset or a venv in the workspace?"
            )
        try:
            data = self.sandbox.filesystem.read_bytes(_EXPORT_PATH)
        except Exception as exc:
            raise InfrastructureError(f"export download failed: {type(exc).__name__}: {exc}") from exc
        return _reprefix(data, "snapshot")

    def import_tarball(self, data: bytes, prefix: str = "snapshot") -> None:
        """Unpack the `prefix/` members of a tarball into the workspace root."""
        try:
            self.sandbox.filesystem.write_bytes(data, _SEED_PATH)
        except Exception as exc:
            raise InfrastructureError(f"seed upload failed: {type(exc).__name__}: {exc}") from exc
        cmd = f"tar xzf {_SEED_PATH} --strip-components=1 {shlex.quote(prefix)}"  # cwd is the root
        r = self.exec(cmd, timeout_s=_EXPORT_TIMEOUT_S)
        if r.returncode != 0:
            raise InfrastructureError(f"import failed: {r.stderr[:500]}")

    def write_assignment(self, spec_md: str, interface_md: str, public_tests: dict[str, str]) -> None:
        self.write_file(f"{ASSIGNMENT_DIR}/spec.md", spec_md)
        self.write_file(f"{ASSIGNMENT_DIR}/interface.md", interface_md)
        for name, body in public_tests.items():
            self.write_file(f"{ASSIGNMENT_DIR}/tests/public/{name}", body)


def create_builder_sandbox(config, spec_md: str, wall_clock_s: int):
    """A CPU sandbox for the builder: no secrets, outbound network limited to the configured
    domains plus any host the spec links to, lifetime a little past the wall-clock cap."""
    import modal

    from paper2code.sandbox.modal_app import builder_image

    domains = sorted(set(config.sandbox_allowed_domains) | set(extract_domains(spec_md)))
    lifetime = int(wall_clock_s) + 900
    return modal.Sandbox.create(
        "sleep", str(lifetime),
        app=modal.App.lookup(config.modal_app_name, create_if_missing=True),
        image=builder_image, timeout=lifetime, cpu=config.sandbox_cpu, memory=config.sandbox_memory_mb,
        workdir=ROOT, outbound_domain_allowlist=domains,
    )
