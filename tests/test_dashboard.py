"""The static dashboard: index over every run, one page per run, escaped text, hidden tests never copied."""
import json
import shutil
from datetime import date

from paper2code.dashboard.build import build_site, collect_runs
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Caps, Paper, RunError, create_run
from paper2code.manager.verdict import Flag, Verdict


def _completed(tmp_path, canary_dir, day, title="EMA denoising canary"):
    rec = create_run(tmp_path, day, Caps(), 10.0)
    rec.paper = Paper("canary-0001", title, "https://example.invalid/canary")
    rec.stage = "report"
    rec.outcome = Outcome.COMPLETED_SUSPICIOUS
    rec.budget.spent_usd = 1.5
    rec.budget.gpu_seconds = 7.0
    rec.counters.test_runs_used = 2
    rec.save()
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    shutil.copytree(canary_dir / "reference", rec.run_dir / "workspace")
    Verdict(outcome=Outcome.COMPLETED_SUSPICIOUS, hidden_passed=["h::a"],
            flags=[Flag("hardcoded_result", "canary_method.py", 18, "<b>note</b>")],
            summary="looks fine & dandy", confidence=0.9).write(rec.run_dir)
    (rec.run_dir / "summary.md").write_text(f"# Run {rec.run_id}\n\n- **Paper:** {title}\n", encoding="utf-8")
    (rec.run_dir / "build.log").write_text(json.dumps({"ts": "t", "event": "session_start"}) + "\n", encoding="utf-8")
    return rec


def test_collect_runs_sorts_newest_first_and_reads_fields(tmp_path, canary_dir):
    _completed(tmp_path, canary_dir, date(2026, 10, 6))
    b = _completed(tmp_path, canary_dir, date(2026, 10, 7))
    rows = collect_runs(tmp_path)
    assert [r.run_id for r in rows] == [b.run_id, "2026-10-06"]
    assert rows[0].outcome == "completed_suspicious" and rows[0].spent_usd == 1.5 and rows[0].flags == 1 and rows[0].confidence == 0.9


def test_dashboard_renders_runs_without_verdict_or_summary(tmp_path, canary_dir):
    rec = create_run(tmp_path, date(2026, 10, 5), Caps(), 10.0)
    rec.stage = "fetch"
    rec.outcome = Outcome.ERROR
    rec.error = RunError("fetch", "exception", "ConnectionError: arxiv down")
    rec.save()
    (tmp_path / "2026-10-04").mkdir()
    (tmp_path / "2026-10-04" / "run.json").write_text("{not json", encoding="utf-8")
    _completed(tmp_path, canary_dir, date(2026, 10, 7))
    out = build_site(tmp_path)
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "2026-10-05" in index and "error" in index and "arxiv down" in index
    assert "2026-10-04" in index and "unreadable" in index
    page = (out / "runs" / "2026-10-05" / "index.html").read_text(encoding="utf-8")
    assert "no verdict" in page.lower() and "ConnectionError" in page


def test_dashboard_escapes_untrusted_text(tmp_path, canary_dir):
    rec = _completed(tmp_path, canary_dir, date(2026, 10, 7), title='<script>alert(1)</script> & "quotes"')
    out = build_site(tmp_path)
    index = (out / "index.html").read_text(encoding="utf-8")
    page = (out / "runs" / rec.run_id / "index.html").read_text(encoding="utf-8")
    for text in (index, page):
        assert "<script>alert(1)</script>" not in text and "&lt;script&gt;" in text
    assert "<b>note</b>" not in page and "&lt;b&gt;note&lt;/b&gt;" in page
    assert "&amp; dandy" in page


def test_dashboard_copies_drilldown_files_but_never_hidden_tests(tmp_path, canary_dir):
    rec = _completed(tmp_path, canary_dir, date(2026, 10, 7))
    out = build_site(tmp_path)
    run_out = out / "runs" / rec.run_id
    assert (run_out / "summary.md.txt").exists() and (run_out / "verdict.json.txt").exists()
    assert (run_out / "scope" / "spec.md.txt").exists() and (run_out / "scope" / "tests" / "public" / "test_claim.py.txt").exists()
    assert (run_out / "workspace" / "canary_method.py.txt").exists()
    assert not any("hidden" in p.as_posix() for p in run_out.rglob("*"))
    page = (run_out / "index.html").read_text(encoding="utf-8")
    assert 'href="summary.md.txt"' in page and "tests/hidden" not in page


def test_build_site_is_idempotent_and_uses_docs_by_default(tmp_path, canary_dir):
    _completed(tmp_path, canary_dir, date(2026, 10, 7))
    out1 = build_site(tmp_path)
    out2 = build_site(tmp_path)
    assert out1 == out2 == tmp_path / "docs"
    assert (out2 / "index.html").exists() and (out2 / ".nojekyll").exists()
    assert [r.run_id for r in collect_runs(tmp_path)] == ["2026-10-07"]  # the docs folder is not a run
