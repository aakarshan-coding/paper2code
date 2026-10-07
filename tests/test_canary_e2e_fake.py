"""Spec 15 and 16: the whole pipeline from fetch to completed with no model and no GPU."""
from pathlib import Path

import httpx

from paper2code.arxiv.http import PoliteClient
from paper2code.cli import main
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import RunRecord
from paper2code.manager.verdict import Verdict

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _patch_http(monkeypatch):
    def handler(request):
        url = str(request.url)
        if url.startswith("https://rss.arxiv.org/rss/cs.LG"):
            return httpx.Response(200, content=(FIX / "rss_cs_LG.xml").read_bytes(), request=request)
        if url.startswith("https://rss.arxiv.org/rss/"):
            return httpx.Response(200, content=b'<rss version="2.0"><channel><title>x</title></channel></rss>', request=request)
        if "/html/" in url:
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        return httpx.Response(404, request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)
    monkeypatch.setattr("paper2code.arxiv.http.make_polite_client", lambda ctx: client)


def test_fake_pipeline_reaches_completed(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    assert main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--until", "scope", "--llm", "fake"]) == 0
    assert RunRecord.load(run_dir).stage == "scope"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "reference"), "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.outcome is Outcome.COMPLETED and rec.stage == "report"
    assert Verdict.load(run_dir).hidden_failed == []
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "build.log", "candidates.jsonl", "papers.jsonl", "run.json", "scope", "scope_attempts.jsonl",
        "selected.json", "summary.md", "verdict.json", "workspace",
    ]


def test_fake_pipeline_catches_hardcoded_builder(tmp_path, canary_dir, monkeypatch):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-07"])
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", str(canary_dir / "hardcoded"), "--llm", "fake"]) == 0
    assert RunRecord.load(run_dir).outcome is Outcome.HIDDEN_FAILED
