"""A workspace that rewrites pytest's reports in-process must not be reported as passing by either runner."""
from paper2code.sandbox.remote_tests import build_payload, execute_tests, result_from_dict, tar_directory
from paper2code.sandbox.runner import LocalTestRunner


def test_local_runner_catches_in_process_report_tampering(canary_dir):
    r = LocalTestRunner(timeout_s=120).run(canary_dir / "pytest_patched", canary_dir / "scope" / "tests" / "public")
    assert r.all_passed is False
    assert len(r.altered) == 3 and all("test_method_halves_mse" in t for t in r.altered)  # the claim tests raised
    assert set(r.altered) <= set(r.failed) and not (set(r.altered) & set(r.passed))
    assert "altered in-process" in r.messages[r.altered[0]]
    assert len(r.passed) == 4  # the unit tests genuinely pass; they are not touched


def test_remote_executor_catches_in_process_report_tampering(canary_dir):
    payload = build_payload(tar_directory(canary_dir / "pytest_patched", "snapshot"), canary_dir / "scope" / "tests" / "hidden", max_bytes=10_000_000)
    d = execute_tests(payload, timeout_s=120)
    r = result_from_dict(d, gpu_seconds=1.0)
    assert r.all_passed is False and len(r.altered) == 5 and d["altered"] == list(r.altered)


def test_cross_check_ignores_expected_exceptions(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "mod.py").write_text("def boom():\n    raise ValueError('x')\n\ndef ok():\n    return 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_t.py").write_text(
        "import pytest\nfrom mod import boom, ok\n\n\ndef test_raises():\n    with pytest.raises(ValueError):\n        boom()\n\n\n"
        "@pytest.mark.xfail\ndef test_xfail():\n    boom()\n\n\n@pytest.mark.skip\ndef test_skip():\n    boom()\n\n\ndef test_ok():\n    assert ok() == 1\n",
        encoding="utf-8",
    )
    r = LocalTestRunner(timeout_s=120).run(ws, tests)
    assert r.altered == () and sorted(r.passed) == ["test_t::test_ok", "test_t::test_raises"]
    assert sorted(r.failed) == ["test_t::test_skip", "test_t::test_xfail"]  # not passed, as before; not "altered"


def test_reference_workspace_is_not_altered(canary_dir):
    r = LocalTestRunner(timeout_s=120).run(canary_dir / "reference", canary_dir / "scope" / "tests" / "hidden")
    assert r.all_passed and r.altered == ()
