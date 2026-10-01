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


def test_run_requires_no_gpu_flag(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--run", str(run_dir), "--builder", "stub", "--reference", str(canary_dir / "reference")])
    assert exc.value.code == 2
    assert "build step 4" in capsys.readouterr().err


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


def test_pipeline_exception_exits_1(tmp_path, canary_dir, capsys):
    run_dir = _init(tmp_path, canary_dir, capsys)
    rc = main(["run", "--run", str(run_dir), "--no-gpu", "--builder", "agent"])
    assert rc == 1
    assert "build step 4" in capsys.readouterr().err
