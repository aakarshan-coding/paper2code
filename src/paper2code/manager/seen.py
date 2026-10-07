"""seen.jsonl at the root of runs/: every arXiv id the loop has graded, so it is never graded twice."""
from __future__ import annotations

import json
from pathlib import Path

from paper2code.manager.record import utcnow

SEEN_FILE = "seen.jsonl"


def load_seen(runs_root: Path) -> set[str]:
    path = runs_root / SEEN_FILE
    if not path.exists():
        return set()
    return {json.loads(line)["arxiv_id"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def append_seen(runs_root: Path, arxiv_ids: list[str], run_id: str) -> None:
    runs_root.mkdir(parents=True, exist_ok=True)
    ts = utcnow()
    with (runs_root / SEEN_FILE).open("a", encoding="utf-8") as fh:
        for arxiv_id in arxiv_ids:
            fh.write(json.dumps({"arxiv_id": arxiv_id, "run_id": run_id, "ts": ts}) + "\n")
