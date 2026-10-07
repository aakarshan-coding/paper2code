import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from paper2code.agents.scout.fake import fake_scout_responder
from paper2code.agents.scout.schemas import EligibilityBatch, EligibilityVerdict, Scorecard
from paper2code.arxiv.feed import RSS_URL
from paper2code.arxiv.http import PoliteClient
from paper2code.config import Config
from paper2code.llm.base import LLMError
from paper2code.llm.fake import FakeChatModel
from paper2code.manager import candidates
from paper2code.manager.graph import RunContext, run_pipeline, run_stage
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, create_run
from paper2code.manager.seen import append_seen, load_seen
from paper2code.manager.stages.fetch import PAPERS_FILE
from paper2code.manager.stages.select import SELECTED_FILE

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _http(html_404_for=()):
    def handler(request):
        url = str(request.url)
        if url == RSS_URL.format(category="cs.LG"):
            return httpx.Response(200, content=(FIX / "rss_cs_LG.xml").read_bytes(), request=request)
        if url == RSS_URL.format(category="stat.ML"):
            return httpx.Response(200, content=(FIX / "rss_stat_ML.xml").read_bytes(), request=request)
        if "/html/" in url:
            if any(i in url for i in html_404_for):
                return httpx.Response(404, request=request)
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        if "/pdf/" in url:
            return httpx.Response(404, request=request)
        return httpx.Response(404, request=request)

    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)


def _scout(eligible_ids, testability_by_id=None, fail_pass_two_for=None):
    testability_by_id = testability_by_id or {}

    def responder(role, instructions, user, schema):
        if schema is EligibilityBatch:
            ids = [line.split(None, 1)[1].strip() for line in user.splitlines() if line.startswith("ID:")]
            return EligibilityBatch(verdicts=[
                EligibilityVerdict(arxiv_id=i, eligible=i in eligible_ids, reason="" if i in eligible_ids else "not_a_method", confidence=0.9 if i in eligible_ids else 0.1)
                for i in ids
            ])
        pid = user.splitlines()[0].split(None, 1)[1].strip()
        if fail_pass_two_for and pid == fail_pass_two_for:
            raise LLMError("You have no credits remaining.")
        return Scorecard(testability=testability_by_id.get(pid, 3), difficulty="easy", est_gpu_hours=0.5, est_usd=0.5, claim="c", dataset="d yes", reason="r")

    return FakeChatModel(responder)


def _ctx(tmp_path, http=None, llm=None, **cfg):
    config = Config(runs_root=tmp_path, categories=["cs.LG", "stat.ML"], **cfg)
    return RunContext(config=config, http=http or _http(), chat_model=llm, until="select")


def _new_run(tmp_path):
    return create_run(tmp_path, date(2026, 10, 6), Caps(), 10.0)


def test_seen_roundtrip(tmp_path):
    assert load_seen(tmp_path) == set()
    append_seen(tmp_path, ["a", "b"], "2026-10-06")
    append_seen(tmp_path, ["c"], "2026-10-07")
    assert load_seen(tmp_path) == {"a", "b", "c"}
    rows = [json.loads(l) for l in (tmp_path / "seen.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["run_id"] == "2026-10-06" and "ts" in rows[0]


def test_fetch_writes_papers_and_drops_seen(tmp_path):
    append_seen(tmp_path, ["2610.03727"], "earlier")
    rec = _new_run(tmp_path)
    final = run_stage("fetch", rec.run_dir, _ctx(tmp_path))
    assert final.stage == "fetch" and final.outcome is None
    rows = [json.loads(l) for l in (rec.run_dir / PAPERS_FILE).read_text(encoding="utf-8").splitlines()]
    assert [r["arxiv_id"] for r in rows] == ["2610.03769", "2610.03800"]


def test_fetch_with_nothing_new_is_no_candidates(tmp_path):
    append_seen(tmp_path, ["2610.03769", "2610.03800", "2610.03727"], "earlier")
    rec = _new_run(tmp_path)
    final = run_stage("fetch", rec.run_dir, _ctx(tmp_path))
    assert final.outcome is Outcome.NO_CANDIDATES


def test_score_two_passes_rows_budget_and_seen(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03727"}, {"2610.03727": 5}), max_fulltext_candidates=10)
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is None
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    p1 = [r for r in rows if r["pass"] == 1]
    p2 = [r for r in rows if r["pass"] == 2]
    assert [r["arxiv_id"] for r in p1] == ["2610.03769", "2610.03800", "2610.03727"]
    assert [r["eligible"] for r in p1] == [True, False, True]
    assert sorted(r["arxiv_id"] for r in p2) == ["2610.03727", "2610.03769"]
    assert {r["arxiv_id"]: r["testability"] for r in p2} == {"2610.03727": 5, "2610.03769": 3}
    assert all(r["fulltext_source"] == "html" and r["model"] == "fake" for r in p2)
    assert final.budget.spent_tokens > 0 and final.budget.spent_usd == 0.0
    assert load_seen(tmp_path) == {"2610.03769", "2610.03800", "2610.03727"}


def test_score_caps_full_text_by_confidence(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03800", "2610.03727"}), max_fulltext_candidates=2)
    run_stage("fetch", rec.run_dir, ctx)
    run_stage("score", rec.run_dir, ctx)
    p2 = [r for r in candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE) if r["pass"] == 2]
    assert len(p2) == 2


def test_score_with_nothing_eligible_is_no_candidates(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout(set()))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is Outcome.NO_CANDIDATES
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    assert len(rows) == 3 and all(r["pass"] == 1 for r in rows)


def test_score_continues_when_one_fulltext_fails(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, http=_http(html_404_for=("2610.03769",)), llm=_scout({"2610.03769", "2610.03727"}))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is None
    p2 = {r["arxiv_id"]: r for r in candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE) if r["pass"] == 2}
    assert p2["2610.03769"]["error"].startswith("fulltext_unavailable")
    assert p2["2610.03727"]["testability"] == 3


def test_score_api_error_ends_run_as_error_and_keeps_partial_rows(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03727"}, fail_pass_two_for="2610.03727"))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is Outcome.ERROR
    assert (final.error.stage, final.error.reason) == ("score", "api_error")
    assert "no credits" in final.error.message
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    assert len([r for r in rows if r["pass"] == 1]) == 3  # pass one was kept
    assert final.budget.spent_tokens > 0  # pass-one usage was recorded before the failure
    assert final.stage == "score"


def test_select_writes_shortlist_and_sets_paper(tmp_path):
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769", "2610.03800", "2610.03727"}, {"2610.03800": 5, "2610.03727": 4}), shortlist_size=2)
    final = run_pipeline(rec.run_dir, ctx)
    assert final.stage == "select" and final.outcome is None
    sel = json.loads((rec.run_dir / SELECTED_FILE).read_text(encoding="utf-8"))
    assert sel["policy"] == {"name": "select_v1_testability", "version": "1"}
    assert [c["arxiv_id"] for c in sel["shortlist"]] == ["2610.03800", "2610.03727"]
    assert final.policy.name == "select_v1_testability"
    assert final.paper.arxiv_id == "2610.03800" and final.paper.title == "A Cross-Listed Paper"
    assert final.paper.url == "https://arxiv.org/abs/2610.03800"


def test_select_over_budget_is_no_candidates(tmp_path):
    rec = create_run(tmp_path, date(2026, 10, 6), Caps(), limit_usd=0.1)
    ctx = _ctx(tmp_path, llm=_scout({"2610.03769"}))
    final = run_pipeline(rec.run_dir, ctx)
    assert final.outcome is Outcome.NO_CANDIDATES
    assert json.loads((rec.run_dir / SELECTED_FILE).read_text(encoding="utf-8"))["shortlist"] == []


def test_fake_llm_name_builds_fake_model(tmp_path):
    from paper2code.llm.factory import make_chat_model
    from paper2code.llm.fake import FakeChatModel as Fake

    ctx = RunContext(config=Config(runs_root=tmp_path), llm="fake")
    model = make_chat_model(ctx)
    assert isinstance(model, Fake)
    assert model.responder is fake_scout_responder
    with pytest.raises(ValueError, match="unknown llm"):
        make_chat_model(RunContext(config=Config(runs_root=tmp_path), llm="gemini"))


def test_score_resume_after_crash_does_not_duplicate_rows(tmp_path):
    rec = _new_run(tmp_path)
    base = _scout({"2610.03769", "2610.03800", "2610.03727"}, {"2610.03800": 5})
    orig = base.responder
    calls = {"cards": 0}

    def responder(role, instructions, user, schema):
        if schema is Scorecard:
            calls["cards"] += 1
            if calls["cards"] == 2:
                raise RuntimeError("network blip")
        return orig(role, instructions, user, schema)

    llm = FakeChatModel(responder)
    ctx = _ctx(tmp_path, llm=llm)
    run_stage("fetch", rec.run_dir, ctx)
    with pytest.raises(RuntimeError, match="network blip"):
        run_stage("score", rec.run_dir, ctx)
    pass_one_calls = len([c for c in llm.calls if c[2] is EligibilityBatch])
    final = run_stage("score", rec.run_dir, ctx)
    assert final.stage == "score" and final.outcome is None
    rows = candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE)
    p1 = [r["arxiv_id"] for r in rows if r["pass"] == 1]
    p2 = [r["arxiv_id"] for r in rows if r["pass"] == 2]
    assert sorted(p1) == ["2610.03727", "2610.03769", "2610.03800"]
    assert sorted(p2) == ["2610.03727", "2610.03769", "2610.03800"]
    assert len([c for c in llm.calls if c[2] is EligibilityBatch]) == pass_one_calls  # pass one not re-billed
    assert calls["cards"] == 4  # 1 scored + 1 crashed, then the 2 remaining
    run_stage("select", rec.run_dir, ctx)
    shortlist = json.loads((rec.run_dir / SELECTED_FILE).read_text(encoding="utf-8"))["shortlist"]
    assert len({c["arxiv_id"] for c in shortlist}) == len(shortlist) == 3


def test_select_keeps_one_row_per_paper(tmp_path):
    rec = _new_run(tmp_path)
    rec.stage = "score"
    rec.save()
    row = {"pass": 2, "arxiv_id": "2610.03769", "title": "T", "testability": 4, "est_usd": 0.5, "difficulty": "easy"}
    candidates.append_rows(rec.run_dir / candidates.CANDIDATES_FILE, [row, {**row, "testability": 5}])
    run_stage("select", rec.run_dir, _ctx(tmp_path))
    shortlist = json.loads((rec.run_dir / SELECTED_FILE).read_text(encoding="utf-8"))["shortlist"]
    assert [c["arxiv_id"] for c in shortlist] == ["2610.03769"]
    assert shortlist[0]["testability"] == 5  # last row wins


def test_score_api_error_does_not_mark_papers_seen(tmp_path):
    rec = _new_run(tmp_path)

    def responder(role, instructions, user, schema):
        raise LLMError("You have no credits remaining.")

    ctx = _ctx(tmp_path, llm=FakeChatModel(responder))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is Outcome.ERROR and final.error.reason == "api_error"
    assert load_seen(tmp_path) == set()
    assert candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE) == []


def test_score_scoring_error_papers_are_not_marked_seen(tmp_path):
    rec = _new_run(tmp_path)

    def responder(role, instructions, user, schema):
        if schema is EligibilityBatch:
            ids = [line.split(None, 1)[1].strip() for line in user.splitlines() if line.startswith("ID:")]
            return EligibilityBatch(verdicts=[EligibilityVerdict(arxiv_id=i, eligible=False, reason="not_a_method", confidence=0.1) for i in ids if i != "2610.03800"])
        raise AssertionError("no pass two expected")

    ctx = _ctx(tmp_path, llm=FakeChatModel(responder))
    run_stage("fetch", rec.run_dir, ctx)
    run_stage("score", rec.run_dir, ctx)
    assert load_seen(tmp_path) == {"2610.03769", "2610.03727"}


def test_score_transport_error_on_fulltext_yields_error_row(tmp_path):
    def handler(request):
        url = str(request.url)
        if url == RSS_URL.format(category="cs.LG"):
            return httpx.Response(200, content=(FIX / "rss_cs_LG.xml").read_bytes(), request=request)
        if url == RSS_URL.format(category="stat.ML"):
            return httpx.Response(200, content=(FIX / "rss_stat_ML.xml").read_bytes(), request=request)
        if "2610.03769" in url:
            raise httpx.ReadTimeout("slow", request=request)
        if "/html/" in url:
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        return httpx.Response(404, request=request)

    http = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, max_attempts=2, sleep=lambda s: None)
    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, http=http, llm=_scout({"2610.03769", "2610.03727"}))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is None
    p2 = {r["arxiv_id"]: r for r in candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE) if r["pass"] == 2}
    assert p2["2610.03769"]["error"].startswith("fulltext_unavailable")
    assert p2["2610.03727"]["testability"] == 3


def test_score_bad_model_output_on_one_paper_yields_error_row(tmp_path):
    from paper2code.llm.base import LLMBadOutput

    base = _scout({"2610.03769", "2610.03727"})
    orig = base.responder

    def responder(role, instructions, user, schema):
        if schema is Scorecard and user.startswith("ID: 2610.03769"):
            raise LLMBadOutput("refusal")
        return orig(role, instructions, user, schema)

    rec = _new_run(tmp_path)
    ctx = _ctx(tmp_path, llm=FakeChatModel(responder))
    run_stage("fetch", rec.run_dir, ctx)
    final = run_stage("score", rec.run_dir, ctx)
    assert final.outcome is None
    p2 = {r["arxiv_id"]: r for r in candidates.read_rows(rec.run_dir / candidates.CANDIDATES_FILE) if r["pass"] == 2}
    assert p2["2610.03769"]["error"].startswith("scoring_error")
    assert p2["2610.03727"]["testability"] == 3
