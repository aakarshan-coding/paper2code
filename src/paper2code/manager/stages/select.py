"""Select stage: rank the scorecards with the configured policy, write selected.json."""
from __future__ import annotations

import json

from paper2code.manager import candidates
from paper2code.manager.graph import RunContext
from paper2code.manager.outcomes import Outcome
from paper2code.manager.record import Paper, Policy, RunRecord
from paper2code.policies import get_policy

SELECTED_FILE = "selected.json"


def run(record: RunRecord, ctx: RunContext) -> None:
    rows = [r for r in candidates.read_rows(record.run_dir / candidates.CANDIDATES_FILE) if r.get("pass") == 2]
    policy = get_policy(ctx.config.policy)
    shortlist = policy.select(rows, limit_usd=record.budget.limit_usd, k=ctx.config.shortlist_size)
    (record.run_dir / SELECTED_FILE).write_text(
        json.dumps({"policy": {"name": policy.NAME, "version": policy.VERSION}, "shortlist": shortlist}, indent=2),
        encoding="utf-8",
    )
    record.policy = Policy(name=policy.NAME, version=policy.VERSION)
    if not shortlist:
        record.outcome = Outcome.NO_CANDIDATES
        return
    top = shortlist[0]
    record.paper = Paper(arxiv_id=top["arxiv_id"], title=top.get("title", ""), url=f"https://arxiv.org/abs/{top['arxiv_id']}")
