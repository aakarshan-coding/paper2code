"""The scoper call. Pure function over a ChatModel; the stage writes files and runs checks."""
from __future__ import annotations

from paper2code.agents.scoper.prompts import SCOPER_INSTRUCTIONS, render_scoper_input
from paper2code.agents.scoper.schemas import ScopeDraft
from paper2code.arxiv.models import ArxivPaper
from paper2code.config import Config
from paper2code.llm.base import ChatModel, Usage

ROLE_SCOPER = "scoper"


def draft_scope(
    paper: ArxivPaper, scorecard: dict, fulltext: str, llm: ChatModel, cfg: Config, budget_usd: float, usage: Usage,
) -> ScopeDraft:
    user = render_scoper_input(
        paper, scorecard, fulltext,
        budget_usd=budget_usd, min_seeds=cfg.min_seeds,
        allowed_packages=cfg.allowed_packages, gpu_usd_per_hour=cfg.gpu_usd_per_hour,
    )
    result = llm.parse(ROLE_SCOPER, SCOPER_INSTRUCTIONS, user, ScopeDraft)
    usage.add(result)
    return result.value
