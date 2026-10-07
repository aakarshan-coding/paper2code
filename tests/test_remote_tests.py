"""The pure test-execution core that the Modal GPU function wraps: payload tarballs in, result dict out."""
import io
import random
import tarfile

import pytest

from paper2code.sandbox.remote_tests import PayloadTooLarge, build_payload, execute_tests, result_from_dict, tar_directory


def _members(data: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        return sorted(m.name for m in tf.getmembers() if m.isfile())


def test_tar_directory_prefixes_and_excludes_junk(tmp_path):
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "mod.cpython-312.pyc").write_bytes(b"\x00")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "big.so").write_bytes(b"\x00" * 10)
    (tmp_path / "stray.pyc").write_bytes(b"\x00")
    data = tar_directory(tmp_path, "snapshot")
    assert _members(data) == ["snapshot/mod.py", "snapshot/pkg/__init__.py"]


def test_payload_excludes_junk_and_caps_size(tmp_path, canary_dir):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "canary_method.py").write_text("x = 1\n", encoding="utf-8")
    public = canary_dir / "scope" / "tests" / "public"
    payload = build_payload(tar_directory(ws, "snapshot"), public, max_bytes=10_000_000)
    names = _members(payload)
    assert "snapshot/canary_method.py" in names and "tests/test_claim.py" in names and "tests/test_units.py" in names
    (ws / "data.bin").write_bytes(random.Random(0).randbytes(300_000))  # 300 kB gzip cannot shrink
    with pytest.raises(PayloadTooLarge, match="payload"):
        build_payload(tar_directory(ws, "snapshot"), public, max_bytes=100_000)


def test_execute_tests_runs_the_canary(canary_dir):
    payload = build_payload(tar_directory(canary_dir / "reference", "snapshot"), canary_dir / "scope" / "tests" / "hidden", max_bytes=10_000_000)
    d = execute_tests(payload, timeout_s=120)
    assert d["timed_out"] is False and d["returncode"] == 0
    assert len(d["passed"]) == 5 and d["failed"] == [] and d["errored"] == []
    assert len(d["workspace_sha256"]) == 64 and d["duration_s"] > 0
    r = result_from_dict(d, gpu_seconds=12.5)
    assert r.all_passed and r.gpu_seconds == 12.5 and r.workspace_sha256 == d["workspace_sha256"]


def test_execute_tests_reports_failures_and_messages(canary_dir):
    payload = build_payload(tar_directory(canary_dir / "hardcoded", "snapshot"), canary_dir / "scope" / "tests" / "hidden", max_bytes=10_000_000)
    d = execute_tests(payload, timeout_s=120)
    assert len(d["failed"]) == 5 and all("assert" in m.lower() for m in d["messages"].values())
    r = result_from_dict(d, gpu_seconds=1.0)
    assert not r.all_passed and set(r.failed) == set(d["failed"]) and r.messages == d["messages"]


def test_execute_tests_times_out(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_slow.py").write_text("import time\ndef test_slow():\n    time.sleep(30)\n", encoding="utf-8")
    d = execute_tests(build_payload(tar_directory(ws, "snapshot"), tests, max_bytes=10_000_000), timeout_s=2)
    assert d["timed_out"] is True and result_from_dict(d, 2.0).all_passed is False
