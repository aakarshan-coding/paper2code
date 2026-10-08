"""Live: the real inspector model reviews the canary workspaces. Opt in with PAPER2CODE_LIVE_INSPECT=1.
Costs a few cents per call on the configured inspector model."""
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from paper2code.agents.inspector.bundle import build_bundle
from paper2code.agents.inspector.inspector import review_workspace_with_model
from paper2code.config import load_config
from paper2code.llm.base import Usage
from paper2code.manager.buildlog import BuildLog
from paper2code.manager.record import Caps, create_run

pytestmark = pytest.mark.skipif(os.environ.get("PAPER2CODE_LIVE_INSPECT") != "1", reason="set PAPER2CODE_LIVE_INSPECT=1 to call the inspector model")


def _bundle(tmp_path, canary_dir, implementation):
    rec = create_run(tmp_path / implementation, date(2026, 10, 7), Caps(), 10.0)
    shutil.copytree(canary_dir / "scope", rec.run_dir / "scope")
    shutil.copytree(canary_dir / implementation, rec.run_dir / "workspace")
    shutil.copy(canary_dir / "paper.md", rec.run_dir / "paper.md")
    BuildLog(rec.run_dir / "build.log").append({"event": "session_start", "builder": "stub"})
    rec.save()
    return build_bundle(rec.run_dir, 120_000)


def test_live_inspector_on_reference_and_hardcoded(tmp_path, canary_dir):
    from paper2code.llm.openai_client import OpenAIChatModel

    cfg = load_config(Path("config.yaml"))
    llm = OpenAIChatModel(cfg.models, cfg.prices)
    usage = Usage()
    good = review_workspace_with_model(_bundle(tmp_path, canary_dir, "reference"), llm, usage)
    bad = review_workspace_with_model(_bundle(tmp_path, canary_dir, "hardcoded"), llm, usage)
    print("reference:", good.method_matches_paper, good.confidence, [(f.kind, f.file, f.line) for f in good.flags], "|", good.summary)
    print("hardcoded:", bad.method_matches_paper, bad.confidence, [(f.kind, f.file, f.line) for f in bad.flags], "|", bad.summary)
    print("cost USD", round(usage.cost_usd, 4), "| tokens in/out", usage.input_tokens, usage.output_tokens)
    assert good.method_matches_paper and not any(f.kind == "hardcoded_result" for f in good.flags)
    assert any(f.kind == "hardcoded_result" for f in bad.flags)
