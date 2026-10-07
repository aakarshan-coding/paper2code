"""TestRunner backed by the deployed Modal GPU function. The tests travel inside the call's payload
and never touch the builder's sandbox; the function's wall time is the GPU charge."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from paper2code.sandbox.remote_tests import PayloadTooLarge, build_payload, result_from_dict, tar_directory
from paper2code.sandbox.runner import TestRunResult


class RemoteError(Exception):
    """The remote test function failed for a reason other than a timeout."""


def _default_remote(app_name: str) -> Callable[[bytes, int], dict]:
    import modal

    fn = modal.Function.from_name(app_name, "run_tests_remote")
    return lambda payload, timeout_s: fn.remote(payload, timeout_s)


class ModalTestRunner:
    """`snapshot_source` supplies the workspace tarball (a sandbox export); without it the local
    `workspace` directory is tarred. `remote` is the function to call; it defaults to the deployed one."""

    def __init__(
        self,
        timeout_s: int,
        max_payload_bytes: int,
        remote: Callable[[bytes, int], dict] | None = None,
        snapshot_source: Callable[[], bytes] | None = None,
        app_name: str = "paper2code",
    ) -> None:
        self.timeout_s = timeout_s
        self.max_payload_bytes = max_payload_bytes
        self._remote = remote
        self.snapshot_source = snapshot_source
        self.app_name = app_name

    @property
    def remote(self) -> Callable[[bytes, int], dict]:
        if self._remote is None:
            self._remote = _default_remote(self.app_name)
        return self._remote

    def run(self, workspace: Path, tests_dir: Path) -> TestRunResult:
        snapshot = self.snapshot_source() if self.snapshot_source else tar_directory(workspace, "snapshot")
        try:
            payload = build_payload(snapshot, tests_dir, self.max_payload_bytes)
        except PayloadTooLarge as exc:
            return TestRunResult((), (), -2, False, 0.0, 0.0, f"run_tests refused: {exc}", "")
        start = time.monotonic()
        try:
            d = self.remote(payload, self.timeout_s)
        except Exception as exc:
            elapsed = time.monotonic() - start
            if type(exc).__name__.endswith("TimeoutError"):
                return TestRunResult((), (), -1, True, elapsed, elapsed, f"remote test function timed out: {exc}", "")
            raise RemoteError(f"{type(exc).__name__}: {exc}") from exc
        return result_from_dict(d, gpu_seconds=float(d.get("duration_s", 0.0)))
