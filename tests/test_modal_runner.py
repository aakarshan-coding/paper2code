"""ModalTestRunner with the remote function replaced by a local callable; no Modal in the unit suite."""
import time
from pathlib import Path

import pytest

from paper2code.sandbox.modal_runner import ModalTestRunner, RemoteError
from paper2code.sandbox.remote_tests import execute_tests, tar_directory


def _local_remote(payload: bytes, timeout_s: int) -> dict:
    return execute_tests(payload, timeout_s)


def test_runner_round_trips_through_a_remote_callable(canary_dir):
    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=_local_remote)
    r = runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")
    assert r.all_passed and len(r.passed) == 7 and r.gpu_seconds == r.duration_s > 0
    assert len(r.workspace_sha256) == 64


def test_runner_uses_snapshot_source_when_given(canary_dir, tmp_path):
    calls = {"n": 0}

    def source():
        calls["n"] += 1
        return tar_directory(canary_dir / "reference", "snapshot")

    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=_local_remote, snapshot_source=source)
    r = runner.run(tmp_path / "ignored", canary_dir / "scope" / "tests" / "hidden")
    assert r.all_passed and calls["n"] == 1


def test_payload_too_large_is_a_failed_result_not_an_exception(canary_dir, tmp_path):
    import random

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "blob.bin").write_bytes(random.Random(0).randbytes(200_000))
    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=50_000, remote=_local_remote)
    r = runner.run(ws, canary_dir / "scope" / "tests" / "public")
    assert r.all_passed is False and r.returncode == -2 and "payload" in r.output and r.gpu_seconds == 0.0


def test_runner_maps_function_timeout_to_timed_out_result(canary_dir):
    class FunctionTimeoutError(Exception):
        pass

    def remote(payload, timeout_s):
        time.sleep(0.2)
        raise FunctionTimeoutError("function timed out")

    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=remote)
    r = runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")
    assert r.timed_out is True and r.all_passed is False and r.gpu_seconds >= 0.2


def test_other_remote_failures_raise_remote_error(canary_dir):
    def remote(payload, timeout_s):
        raise RuntimeError("container crashed")

    runner = ModalTestRunner(timeout_s=120, max_payload_bytes=10_000_000, remote=remote)
    with pytest.raises(RemoteError, match="container crashed"):
        runner.run(canary_dir / "reference", canary_dir / "scope" / "tests" / "public")


def test_modal_app_module_declares_the_function():
    src = Path("src/paper2code/sandbox/modal_app.py").read_text(encoding="utf-8")
    assert 'modal.App("paper2code")' in src and 'name="run_tests_remote"' in src and "execute_tests(payload, timeout_s)" in src
    assert "add_local_python_source" in src and "download.pytorch.org/whl/cpu" in src
