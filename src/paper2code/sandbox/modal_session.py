"""The builder's sandbox lifetime: create it, seed it from any earlier workspace, hand the agent a
Workspace and the manager a runner that snapshots from it, and on any exit export the workspace
back into the run record and destroy the sandbox."""
from __future__ import annotations

import io
import shutil
import tarfile
from contextlib import contextmanager
from pathlib import Path

from paper2code.manager.buildlog import BuildLog
from paper2code.sandbox.modal_runner import ModalTestRunner
from paper2code.sandbox.modal_workspace import ModalWorkspace, create_builder_sandbox
from paper2code.sandbox.remote_tests import tar_directory


class SandboxFailed(Exception):
    """The sandbox could not be created or stopped working; an infrastructure outcome, not the builder's."""


def _has_files(path: Path) -> bool:
    return path.exists() and any(p.is_file() for p in path.rglob("*"))


def _export_to(run_dir: Path, data: bytes) -> None:
    """Replace run_dir/workspace with the `snapshot/` members of the tarball."""
    dest = run_dir / "workspace"
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    base = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.startswith("snapshot/"):
                continue
            target = (dest / Path(*Path(m.name).parts[1:])).resolve()
            if base not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tf.extractfile(m).read())


@contextmanager
def modal_build_session(record, ctx, log: BuildLog, sandbox_factory=None, workspace_cls=None, runner_cls=None):
    """Yields (workspace, runner). The three factories exist so tests can stand in for Modal."""
    sandbox_factory = sandbox_factory or create_builder_sandbox
    workspace_cls = workspace_cls or ModalWorkspace
    runner_cls = runner_cls or ModalTestRunner
    cfg = ctx.config
    run_dir = record.run_dir
    scope = run_dir / "scope"
    spec_md = (scope / "spec.md").read_text(encoding="utf-8")
    try:
        sandbox = sandbox_factory(cfg, spec_md, record.caps.wall_clock_s)
    except Exception as exc:
        raise SandboxFailed(f"could not create the builder sandbox: {type(exc).__name__}: {exc}") from exc
    log.append({"event": "sandbox", "action": "created", "id": getattr(sandbox, "object_id", None)})
    workspace = workspace_cls(sandbox)
    try:
        if _has_files(run_dir / "workspace"):
            workspace.import_tarball(tar_directory(run_dir / "workspace", "snapshot"))
            log.append({"event": "sandbox", "action": "seeded_from_export"})
        public = {p.name: p.read_text(encoding="utf-8") for p in sorted((scope / "tests" / "public").glob("test_*.py"))}
        workspace.write_assignment(spec_md, (scope / "interface.md").read_text(encoding="utf-8"), public)
        runner = runner_cls(
            timeout_s=cfg.run_tests_timeout_s, max_payload_bytes=cfg.payload_max_mb * 2**20,
            snapshot_source=workspace.export_tarball, app_name=cfg.modal_app_name,
        )
        yield workspace, runner
    finally:
        try:
            _export_to(run_dir, workspace.export_tarball())
            log.append({"event": "sandbox", "action": "exported"})
        except Exception as exc:  # the run record keeps whatever was exported last; the log says why
            log.append({"event": "sandbox", "action": "export_failed", "message": f"{type(exc).__name__}: {exc}"})
        try:
            sandbox.terminate()
        finally:
            log.append({"event": "sandbox", "action": "terminated"})
