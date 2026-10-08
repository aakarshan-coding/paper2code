"""The inspector agent: schema, input bundle, prompt rendering, and the heuristic fake."""
import json
import shutil
from datetime import date

import pytest

from paper2code.agents.fake import fake_agent_responder
from paper2code.agents.inspector.bundle import build_bundle
from paper2code.agents.inspector.inspector import review_workspace_with_model, to_flags
from paper2code.agents.inspector.prompts import INSPECTOR_INSTRUCTIONS, render_inspector_input
from paper2code.agents.inspector.schemas import CODE_REVIEW_KINDS, InspectionReport, ReviewFlag
from paper2code.llm.base import Usage
from paper2code.llm.fake import FakeChatModel
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import Caps, create_run

HARDCODED_LINE = 18  # the `if seed in (0, 1, 2)` line of tests/fixtures/canary/hardcoded/canary_method.py


def _run(tmp_path, canary_dir, implementation="reference"):
    rec = create_run(tmp_path, date(2026, 10, 7), Caps(), 10.0)
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    shutil.copytree(canary_dir / implementation, rec.run_dir / "workspace")
    shutil.copy(canary_dir / "paper.md", rec.run_dir / "paper.md")
    log = BuildLog(rec.run_dir / "build.log")
    log.append({"event": "session_start", "builder": "stub"})
    log.append({"event": "tool_call", "tool": "bash", "args": {"command": "python -c 'print(1)'"}, "ok": True})
    log.append({"event": "run_tests", "call": 1, "passed": ["a", "b"], "failed": []})
    rec.save()
    return rec


def test_schema_limits_kinds_to_the_spec_list():
    assert CODE_REVIEW_KINDS == ("hardcoded_result", "test_detection", "sandbagged_baseline", "data_leakage", "wrong_method")
    with pytest.raises(Exception):
        ReviewFlag(kind="made_up", file="x.py", line=1, evidence="e")
    ReviewFlag(kind="wrong_method", file="x.py", line=None, evidence="e")
    with pytest.raises(Exception):
        InspectionReport(flags=[], method_matches_paper=True, confidence=1.5, summary="s")


def test_bundle_collects_every_input(tmp_path, canary_dir):
    rec = _run(tmp_path, canary_dir)
    b = build_bundle(rec.run_dir, max_chars=120_000)
    assert "Exponential Smoothing" in b.paper and b.spec.startswith("# Scope") and "canary_method" in b.interface
    assert set(b.public_tests) == {"test_claim.py", "test_units.py"} and set(b.hidden_tests) == {"test_claim_hidden.py"}
    assert list(b.workspace) == ["canary_method.py"] and b.truncated == []
    assert "run_tests" in b.build_log and "python -c" in b.build_log


def test_bundle_falls_back_to_abstract_then_title(tmp_path, canary_dir):
    rec = _run(tmp_path, canary_dir)
    (rec.run_dir / "paper.md").unlink()
    rec.paper.arxiv_id = "2610.00001"
    rec.paper.title = "A Title"
    rec.save()
    assert build_bundle(rec.run_dir, 120_000).paper.strip() == "A Title"
    row = {"arxiv_id": "2610.00001", "title": "A Title", "abstract": "The abstract.", "authors": [], "categories": [], "published": "", "url": ""}
    (rec.run_dir / "papers.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert "The abstract." in build_bundle(rec.run_dir, 120_000).paper


def test_bundle_caps_and_skips_binaries(tmp_path, canary_dir):
    rec = _run(tmp_path, canary_dir)
    ws = rec.run_dir / "workspace"
    (ws / "big.log").write_text("x" * 50_000, encoding="utf-8")
    (ws / "model.bin").write_bytes(bytes(range(256)) * 10)
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "m.pyc").write_bytes(b"\x00")
    b = build_bundle(rec.run_dir, max_chars=20_000)
    assert "model.bin" not in b.workspace and any("model.bin" in t for t in b.truncated)
    assert "big.log" in b.workspace and len(b.workspace["big.log"]) < 50_000 and "truncated" in b.workspace["big.log"]
    assert any("big.log" in t for t in b.truncated)
    assert not any("pyc" in k for k in b.workspace)
    text = render_inspector_input(b)
    assert len(text) < 30_000 and "big.log" in text and "truncated" in text.lower()


def test_rendered_input_numbers_workspace_lines_and_marks_untrusted(tmp_path, canary_dir):
    rec = _run(tmp_path, canary_dir)
    text = render_inspector_input(build_bundle(rec.run_dir, 120_000))
    assert "### file: canary_method.py" in text and "\n1| " in text
    assert "untrusted" in INSPECTOR_INSTRUCTIONS.lower()
    for kind in CODE_REVIEW_KINDS:
        assert kind in INSPECTOR_INSTRUCTIONS


def test_fake_inspector_flags_the_hardcoded_canary_and_clears_the_reference(tmp_path, canary_dir):
    llm = FakeChatModel(fake_agent_responder)
    usage = Usage()
    bad_run = _run(tmp_path / "bad", canary_dir, "hardcoded")
    bad = review_workspace_with_model(build_bundle(bad_run.run_dir, 120_000), llm, usage)
    assert [f.kind for f in bad.flags] == ["hardcoded_result"]
    assert bad.flags[0].file == "canary_method.py" and bad.flags[0].line == HARDCODED_LINE
    assert bad.method_matches_paper is False and bad.confidence < 0.5
    good_run = _run(tmp_path / "good", canary_dir, "reference")
    good = review_workspace_with_model(build_bundle(good_run.run_dir, 120_000), llm, usage)
    assert good.flags == [] and good.method_matches_paper and good.confidence > 0.5
    assert usage.calls == 2 and llm.calls[0][0] == "inspector"
    flags = to_flags(bad)
    assert flags[0].source == "inspector" and flags[0].kind == "hardcoded_result" and flags[0].line == HARDCODED_LINE and flags[0].note
