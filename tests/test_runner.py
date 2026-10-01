import textwrap

from paper2code.sandbox.runner import LocalTestRunner, TestRunResult, parse_junit


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def test_result_all_passed_rules():
    ok = TestRunResult(("a::t",), (), 0, False, 0.1, 0.0, "")
    assert ok.all_passed
    assert TestRunResult((), (), 0, False, 0.1, 0.0, "").all_passed is False
    assert TestRunResult(("a::t",), ("a::u",), 1, False, 0.1, 0.0, "").all_passed is False
    assert TestRunResult(("a::t",), (), 0, True, 0.1, 0.0, "").all_passed is False
    assert TestRunResult(("a::t",), ("a::u", "a::v"), 1, False, 0.1, 0.0, "").failing_set == {"a::u", "a::v"}


def test_runs_tests_against_workspace_snapshot(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "mod.py", "def add(a, b):\n    return a + b\n")
    _write(tests / "test_mod.py", """
        from mod import add
        def test_add(): assert add(1, 2) == 3
        def test_wrong(): assert add(1, 2) == 4
    """)
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.passed == ("test_mod::test_add",)
    assert result.failed == ("test_mod::test_wrong",)
    assert result.timed_out is False
    assert result.returncode != 0
    assert result.gpu_seconds == 0.0
    assert result.all_passed is False
    assert "test_wrong" in result.output


def test_all_passed_when_everything_passes(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "mod.py", "X = 1\n")
    _write(tests / "test_mod.py", "from mod import X\ndef test_x(): assert X == 1\n")
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.all_passed
    assert result.failed == ()


def test_missing_module_is_not_all_passed(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    ws.mkdir()
    _write(tests / "test_mod.py", "from mod import X\ndef test_x(): assert X == 1\n")
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.all_passed is False
    assert result.passed == ()
    assert result.failed  # the collection error is reported as a failed case


def test_skipped_test_counts_as_not_passed(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    ws.mkdir()
    _write(tests / "test_mod.py", """
        import pytest
        @pytest.mark.skip(reason="hollowed out")
        def test_claim(): assert False
    """)
    result = LocalTestRunner(timeout_s=60).run(ws, tests)
    assert result.all_passed is False
    assert result.failed == ("test_mod::test_claim",)


def test_timeout_is_reported_not_hung(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    ws.mkdir()
    _write(tests / "test_mod.py", "import time\ndef test_hang(): time.sleep(30)\n")
    result = LocalTestRunner(timeout_s=2).run(ws, tests)
    assert result.timed_out is True
    assert result.all_passed is False
    assert result.duration_s < 20


def test_workspace_is_snapshotted_not_mutated(tmp_path):
    ws = tmp_path / "ws"
    tests = tmp_path / "tests"
    _write(ws / "mod.py", "X = 1\n")
    _write(tests / "test_mod.py", """
        import pathlib
        def test_write(): pathlib.Path("evidence.txt").write_text("x")
    """)
    LocalTestRunner(timeout_s=60).run(ws, tests)
    assert not (ws / "evidence.txt").exists()
    assert sorted(p.name for p in ws.iterdir()) == ["mod.py"]


def test_parse_junit(tmp_path):
    xml = tmp_path / "r.xml"
    xml.write_text("""<?xml version="1.0"?>
    <testsuites><testsuite name="pytest">
      <testcase classname="test_a" name="test_ok" time="0.01"/>
      <testcase classname="test_a" name="test_bad" time="0.01"><failure message="m">x</failure></testcase>
      <testcase classname="test_a" name="test_err" time="0.01"><error message="m">x</error></testcase>
      <testcase classname="test_a" name="test_skip" time="0.01"><skipped message="m">x</skipped></testcase>
    </testsuite></testsuites>""", encoding="utf-8")
    passed, failed = parse_junit(xml)
    assert passed == ("test_a::test_ok",)
    assert failed == ("test_a::test_bad", "test_a::test_err", "test_a::test_skip")
