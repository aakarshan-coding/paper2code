import json

import pytest

from paper2code.cli import main
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import RunRecord


def _init(tmp_path, canary_dir, capsys):
    rc = main([
        "init-run", "--scope", str(canary_dir / "scope"), "--paper-id", "canary-0001",
        "--title", "EMA denoising canary", "--runs-root", str(tmp_path), "--date", "2026-09-30",
    ])
    assert rc == 0
    assert capsys.readouterr().out.strip() == f"created {tmp_path / '2026-09-30'}"
    return tmp_path / "2026-09-30"


def test_init_run_seeds_scope_stage(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    rec = RunRecord.load(run_dir)
    assert rec.stage == "scope"
    assert rec.paper.arxiv_id == "canary-0001"
    assert rec.paper.title == "EMA denoising canary"
    manifest = json.loads((run_dir / "scope" / "manifest.json").read_text(encoding="utf-8"))
    assert "tests/hidden/test_claim_hidden.py" in manifest
    assert "tests/public/test_claim.py" in manifest


def test_stub_builder_requires_reference(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub"])
    assert exc.value.code == 2


def test_single_stage_then_resume(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    ref = str(canary_dir / "reference")
    assert main(["build", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", ref]) == 0
    assert RunRecord.load(run_dir).stage == "build"
    assert main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "stub", "--reference", ref]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.stage == "report"
    assert rec.outcome is not None
    assert len(BuildLog(run_dir / "build.log").events("run_tests")) == 1  # build was not re-run
    assert "outcome: completed" in capsys.readouterr().out


def test_pipeline_exception_exits_1(tmp_path, canary_dir, capsys, monkeypatch):
    """A stage that raises makes the CLI exit 1 and print the error. The builder is stubbed so no
    agent session can start inside the unit suite."""
    import paper2code.agents.builder.factory as factory

    def boom(ctx):
        raise RuntimeError("simulated stage failure")

    monkeypatch.setattr(factory, "make_builder", boom)
    run_dir = _init(tmp_path, canary_dir, capsys)
    rc = main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "agent"])
    assert rc == 1
    assert "simulated stage failure" in capsys.readouterr().err


# ---- step 2: fetch / score / select through the CLI ----
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402

from paper2code.arxiv.http import PoliteClient  # noqa: E402
from paper2code.manager.stages.fetch import PAPERS_FILE  # noqa: E402

FIX_ARXIV = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _patch_http(monkeypatch):
    def handler(request):
        url = str(request.url)
        if url.startswith("https://rss.arxiv.org/rss/cs.LG"):
            return httpx.Response(200, content=(FIX_ARXIV / "rss_cs_LG.xml").read_bytes(), request=request)
        if url.startswith("https://rss.arxiv.org/rss/"):
            return httpx.Response(200, content=b'<rss version="2.0"><channel><title>x</title></channel></rss>', request=request)
        if url.startswith("https://export.arxiv.org/api/query"):
            return httpx.Response(200, content=(FIX_ARXIV / "api_by_id.xml").read_bytes(), request=request)
        if "/html/" in url:
            return httpx.Response(200, content=(FIX_ARXIV / "paper.html").read_bytes(), request=request)
        return httpx.Response(404, request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)
    monkeypatch.setattr("paper2code.arxiv.http.make_polite_client", lambda ctx: client)


def test_new_run_then_dry_run_to_select_with_fake_llm(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    assert main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-06"]) == 0
    run_dir = tmp_path / "2026-10-06"
    assert capsys.readouterr().out.strip() == f"created {run_dir}"
    assert main(["run", "--run", str(run_dir), "--until", "select", "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.stage == "select" and rec.outcome is None
    assert rec.paper.arxiv_id in ("2610.03769", "2610.03800")
    assert (run_dir / "candidates.jsonl").exists() and (run_dir / "selected.json").exists()
    assert "stage: select" in capsys.readouterr().out


def test_single_stage_commands_fetch_score_select(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-06"])
    run_dir = tmp_path / "2026-10-06"
    assert main(["fetch", "--run", str(run_dir)]) == 0
    assert RunRecord.load(run_dir).stage == "fetch"
    assert main(["score", "--run", str(run_dir), "--llm", "fake"]) == 0
    assert RunRecord.load(run_dir).stage == "score"
    assert main(["select", "--run", str(run_dir)]) == 0
    assert RunRecord.load(run_dir).stage == "select"


def test_score_by_arxiv_id_creates_single_paper_run(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    assert main(["score", "--arxiv-id", "2610.03769", "--llm", "fake", "--runs-root", str(tmp_path), "--date", "2026-10-06"]) == 0
    run_dir = tmp_path / "2026-10-06"
    rec = RunRecord.load(run_dir)
    assert rec.stage == "score" and rec.outcome is None
    papers = (run_dir / PAPERS_FILE).read_text(encoding="utf-8").splitlines()
    assert len(papers) == 1 and "2610.03769" in papers[0]
    rows = (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2  # pass one and pass two for the one paper


def test_run_until_select_does_not_require_gpu_flags(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-06"])
    assert main(["run", "--run", str(tmp_path / "2026-10-06"), "--until", "fetch"]) == 0


def test_run_until_scope_with_fake_llm_freezes_canary(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    main(["new-run", "--runs-root", str(tmp_path), "--date", "2026-10-07"])
    run_dir = tmp_path / "2026-10-07"
    assert main(["run", "--run", str(run_dir), "--until", "scope", "--llm", "fake"]) == 0
    rec = RunRecord.load(run_dir)
    assert rec.stage == "scope" and rec.outcome is None
    assert (run_dir / "scope" / "manifest.json").exists()
    assert (run_dir / "scope_attempts.jsonl").exists()


def test_scope_by_arxiv_id_creates_scoped_run(tmp_path, monkeypatch, capsys):
    _patch_http(monkeypatch)
    assert main(["scope", "--arxiv-id", "2610.03769", "--llm", "fake", "--runs-root", str(tmp_path), "--date", "2026-10-07"]) == 0
    run_dir = tmp_path / "2026-10-07"
    rec = RunRecord.load(run_dir)
    assert rec.stage == "scope" and rec.outcome is None and rec.paper.arxiv_id == "2610.03769"
    rows = (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
    assert '"model": "forced"' in rows[0]
    assert "stage: scope" in capsys.readouterr().out


def test_agent_builder_needs_no_reference_flag(tmp_path, canary_dir, capsys, monkeypatch):
    """Argument handling only: the real agent builder is replaced so no SDK session is started."""
    import paper2code.agents.builder.factory as factory

    seen = {}

    class NoOpBuilder:
        def build(self, ctx):
            seen["built"] = True

    monkeypatch.setattr(factory, "make_builder", lambda ctx: (seen.__setitem__("builder", ctx.builder), NoOpBuilder())[1])
    run_dir = _init(tmp_path, canary_dir, capsys)
    rc = main(["build", "--run", str(run_dir), "--no-gpu", "--builder", "agent", "--config", str(tmp_path / "absent.yaml")])
    assert rc == 0 and seen == {"builder": "agent", "built": True}
    assert "requires --reference" not in capsys.readouterr().err


def test_without_no_gpu_the_context_selects_modal(tmp_path, canary_dir, capsys, monkeypatch):
    """No --no-gpu: the build stage gets the Modal runner (never called here, the builder is a probe)."""
    import paper2code.agents.builder.factory as factory

    seen = {}

    class Probe:
        def build(self, ctx):
            seen["runner"] = type(ctx.session.runner).__name__

    monkeypatch.setattr(factory, "make_builder", lambda ctx: Probe())
    run_dir = _init(tmp_path, canary_dir, capsys)
    rc = main(["build", "--run", str(run_dir), "--builder", "stub", "--reference", str(canary_dir / "reference"), "--config", str(tmp_path / "absent.yaml")])
    assert rc == 0 and seen["runner"] == "ModalTestRunner"
