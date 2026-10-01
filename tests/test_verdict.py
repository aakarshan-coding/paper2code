import json

from paper2code.manager.outcomes import Outcome
from paper2code.manager.verdict import Flag, Verdict, decide
from paper2code.sandbox.runner import TestRunResult

PASS = TestRunResult(("h::t1", "h::t2"), (), 0, False, 1.0, 0.0, "")
FAIL = TestRunResult(("h::t1",), ("h::t2",), 1, False, 1.0, 0.0, "")
FLAG = Flag(kind="hardcoded_result", file="canary_method.py", line=12, note="lookup table keyed by seed")


def test_completed_when_clean():
    assert decide([], PASS, []) is Outcome.COMPLETED


def test_suspicious_when_flags():
    assert decide([], PASS, [FLAG]) is Outcome.COMPLETED_SUSPICIOUS


def test_hidden_failed_beats_flags():
    assert decide([], FAIL, [FLAG]) is Outcome.HIDDEN_FAILED


def test_tampered_beats_everything():
    assert decide(["tests/hidden/test_h.py"], PASS, []) is Outcome.TESTS_TAMPERED
    assert decide(["spec.md"], FAIL, [FLAG]) is Outcome.TESTS_TAMPERED


def test_timed_out_hidden_run_is_hidden_failed():
    timed_out = TestRunResult((), (), -1, True, 5.0, 0.0, "")
    assert decide([], timed_out, []) is Outcome.HIDDEN_FAILED


def test_verdict_write_and_load(tmp_path):
    v = Verdict(
        outcome=Outcome.COMPLETED_SUSPICIOUS,
        integrity_mismatches=[],
        hidden_passed=["h::t1"],
        hidden_failed=[],
        flags=[FLAG],
        summary="method matches paper; one suspicious constant",
        confidence=0.8,
    )
    path = v.write(tmp_path)
    assert path == tmp_path / "verdict.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["outcome"] == "completed_suspicious"
    assert data["flags"] == [{"kind": "hardcoded_result", "file": "canary_method.py", "line": 12, "note": "lookup table keyed by seed"}]
    assert Verdict.load(tmp_path) == v
