import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from paper2code.agents.scoper.fake import CANARY_DRAFT
from paper2code.agents.scoper.schemas import ScopeDraft, TestFile
from paper2code.arxiv.http import PoliteClient
from paper2code.config import Config
from paper2code.llm.base import LLMBadOutput, LLMError
from paper2code.llm.fake import FakeChatModel
from paper2code.manager.freeze import verify_manifest
from paper2code.manager.graph import RunContext, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, create_run
from paper2code.manager.stages.fetch import PAPERS_FILE
from paper2code.manager.stages.scope import ATTEMPTS_FILE, read_attempts
from paper2code.manager.stages.select import SELECTED_FILE

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"
IDS = ["2610.03769", "2610.03800", "2610.03727"]


def _http(html_404_for=()):
    def handler(request):
        url = str(request.url)
        if "/html/" in url and not any(i in url for i in html_404_for):
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        return httpx.Response(404, request=request)

    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)


def _seed_selected(tmp_path, limit_usd=10.0, ids=IDS):
    rec = create_run(tmp_path, date(2026, 10, 7), Caps(), limit_usd)
    papers = [{"arxiv_id": i, "version": 1, "title": f"Paper {i}", "abstract": "a", "authors": [], "categories": [], "primary_category": "", "announce_type": "new", "published": "", "url": f"https://arxiv.org/abs/{i}"} for i in ids]
    (rec.run_dir / PAPERS_FILE).write_text("".join(json.dumps(p) + "\n" for p in papers), encoding="utf-8")
    shortlist = [{"pass": 2, "arxiv_id": i, "title": f"Paper {i}", "testability": 4, "difficulty": "easy", "est_gpu_hours": 0.5, "est_usd": 0.5, "claim": "c", "dataset": "d yes", "reason": "r"} for i in ids]
    (rec.run_dir / SELECTED_FILE).write_text(json.dumps({"policy": {"name": "select_v1_testability", "version": "1"}, "shortlist": shortlist}), encoding="utf-8")
    rec.stage = "select"
    rec.save()
    return rec


def _scoper(drafts_by_id=None, raise_for=None):
    """drafts_by_id: arxiv_id -> ScopeDraft (default CANARY_DRAFT). raise_for: arxiv_id -> exception."""
    drafts_by_id = drafts_by_id or {}
    raise_for = raise_for or {}

    def responder(role, instructions, user, schema):
        assert schema is ScopeDraft
        pid = user.splitlines()[0].split(None, 1)[1].strip()
        if pid in raise_for:
            raise raise_for[pid]
        return drafts_by_id.get(pid, CANARY_DRAFT)

    return FakeChatModel(responder)


def _ctx(tmp_path, llm, http=None, **cfg):
    return RunContext(config=Config(runs_root=tmp_path, run_tests_timeout_s=120, **cfg), http=http or _http(), chat_model=llm, until="scope")


def test_scope_accepts_first_paper_and_freezes(tmp_path):
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper()))
    assert final.stage == "scope" and final.outcome is None
    assert final.paper.arxiv_id == "2610.03769"
    scope = rec.run_dir / "scope"
    assert (scope / "spec.md").exists() and (scope / "interface.md").exists() and (scope / "manifest.json").exists()
    assert final.scope_manifest_sha256 is not None
    assert verify_manifest(scope, final.scope_manifest_sha256) == []
    rows = read_attempts(rec.run_dir)
    assert len(rows) == 1 and rows[0]["accepted"] is True and rows[0]["arxiv_id"] == "2610.03769"
    assert rows[0]["claim_tests"] == 3 and rows[0]["removed_tests"] == []
    assert final.budget.spent_tokens > 0


def test_scope_falls_through_on_trivial_claim_then_accepts_next(tmp_path):
    trivial_claim = TestFile(path="test_claim.py", content="import pytest\n@pytest.mark.parametrize('seed', [0, 1, 2])\ndef test_claim(seed):\n    assert True\n")
    bad = CANARY_DRAFT.model_copy(update={"public_tests": [CANARY_DRAFT.public_tests[0], trivial_claim]})
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper({"2610.03769": bad})))
    assert final.outcome is None and final.paper.arxiv_id == "2610.03800"
    rows = read_attempts(rec.run_dir)
    assert [(r["arxiv_id"], r["accepted"], r["reason"]) for r in rows] == [("2610.03769", False, "trivial_claim_test"), ("2610.03800", True, None)]
    assert "test_claim::test_claim[0]" in rows[0]["removed_tests"]


def test_scope_rejected_when_shortlist_exhausted(tmp_path):
    over = CANARY_DRAFT.model_copy(update={"est_usd": 99.0})
    rec = _seed_selected(tmp_path, limit_usd=10.0)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper({i: over for i in IDS})))
    assert final.outcome is Outcome.SCOPE_REJECTED
    rows = read_attempts(rec.run_dir)
    assert [r["reason"] for r in rows] == ["over_budget"] * 3
    assert not (rec.run_dir / "scope").exists()


def test_scope_malformed_draft_and_fulltext_failure_are_recorded(tmp_path):
    malformed = CANARY_DRAFT.model_copy(update={"hidden_tests": []})
    rec = _seed_selected(tmp_path)
    ctx = _ctx(tmp_path, _scoper({"2610.03800": malformed}, raise_for={"2610.03727": LLMBadOutput("refusal")}), http=_http(html_404_for=("2610.03769",)))
    final = run_stage("scope", rec.run_dir, ctx)
    assert final.outcome is Outcome.SCOPE_REJECTED
    reasons = [r["reason"] for r in read_attempts(rec.run_dir)]
    assert reasons[0].startswith("fulltext_unavailable") and reasons[1].startswith("malformed_scope") and reasons[2].startswith("malformed_scope")


def test_scope_api_error_ends_run(tmp_path):
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper(raise_for={"2610.03769": LLMError("no credits")})))
    assert final.outcome is Outcome.ERROR and final.error.reason == "api_error" and final.error.stage == "scope"
    assert read_attempts(rec.run_dir) == []


def test_scope_resume_wipes_partial_dir_and_skips_attempted(tmp_path):
    over = CANARY_DRAFT.model_copy(update={"est_usd": 99.0})
    rec = _seed_selected(tmp_path)
    calls = {"n": 0}
    base = _scoper({"2610.03769": over})
    orig = base.responder

    def responder(role, instructions, user, schema):
        calls["n"] += 1
        if calls["n"] == 2:
            (rec.run_dir / "scope").mkdir(exist_ok=True)
            (rec.run_dir / "scope" / "leftover.txt").write_text("partial", encoding="utf-8")
            raise RuntimeError("crash mid-scope")
        return orig(role, instructions, user, schema)

    llm = FakeChatModel(responder)
    ctx = _ctx(tmp_path, llm)
    with pytest.raises(RuntimeError):
        run_stage("scope", rec.run_dir, ctx)
    assert [r["arxiv_id"] for r in read_attempts(rec.run_dir)] == ["2610.03769"]  # first attempt recorded before the crash
    final = run_stage("scope", rec.run_dir, ctx)
    assert final.outcome is None and final.paper.arxiv_id == "2610.03800"
    assert [r["arxiv_id"] for r in read_attempts(rec.run_dir)] == ["2610.03769", "2610.03800"]
    assert calls["n"] == 3  # the rejected first paper was not re-drafted
    assert not (rec.run_dir / "scope" / "leftover.txt").exists()
    assert verify_manifest(rec.run_dir / "scope", final.scope_manifest_sha256) == []


def test_scope_trivial_tests_are_pruned_before_freeze(tmp_path):
    planted = CANARY_DRAFT.model_copy(update={"public_tests": CANARY_DRAFT.public_tests + [TestFile(path="test_planted.py", content="def test_trivial():\n    assert True\n")]})
    rec = _seed_selected(tmp_path)
    final = run_stage("scope", rec.run_dir, _ctx(tmp_path, _scoper({"2610.03769": planted})))
    assert final.outcome is None
    rows = read_attempts(rec.run_dir)
    assert rows[0]["removed_tests"] == ["test_planted::test_trivial"]
    assert "test_trivial" not in (rec.run_dir / "scope" / "tests" / "public" / "test_planted.py").read_text(encoding="utf-8")
    assert verify_manifest(rec.run_dir / "scope", final.scope_manifest_sha256) == []  # frozen after pruning
