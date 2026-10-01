"""The run record: one directory per run, run.json as the single source of truth."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from paper2code.manager.outcomes import Outcome

STAGES: tuple[str, ...] = ("fetch", "score", "select", "scope", "build", "inspect", "report")
RUN_JSON = "run.json"


def stage_index(stage: str | None) -> int:
    """Position of a stage in the pipeline; -1 means no stage has completed."""
    return -1 if stage is None else STAGES.index(stage)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Paper:
    arxiv_id: str = ""
    title: str = ""
    url: str = ""


@dataclass
class Policy:
    name: str = ""
    version: str = ""


@dataclass
class Budget:
    limit_usd: float = 0.0
    spent_usd: float = 0.0
    spent_tokens: int = 0
    gpu_seconds: float = 0.0


@dataclass
class Caps:
    test_runs: int = 25
    wall_clock_s: int = 7200
    stall_n: int = 5


@dataclass
class Counters:
    test_runs_used: int = 0
    attempts: int = 0


@dataclass
class RunError:
    stage: str
    reason: str
    message: str


@dataclass
class RunRecord:
    run_dir: Path
    run_id: str
    started_at: str
    finished_at: str | None = None
    stage: str | None = None
    outcome: Outcome | None = None
    paper: Paper = field(default_factory=Paper)
    policy: Policy = field(default_factory=Policy)
    budget: Budget = field(default_factory=Budget)
    caps: Caps = field(default_factory=Caps)
    counters: Counters = field(default_factory=Counters)
    error: RunError | None = None
    # Freeze anchors, written only by the manager. The inspector verifies scope/ and workspace/
    # against these, so a builder with filesystem access cannot re-freeze its way to `completed`.
    scope_manifest_sha256: str | None = None
    workspace_sha256: str | None = None

    def is_done(self, stage: str) -> bool:
        """True if `stage` (or a later one) has already completed for this run."""
        return stage_index(self.stage) >= stage_index(stage)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("run_dir")
        d["outcome"] = self.outcome.value if self.outcome is not None else None
        return d

    @classmethod
    def from_dict(cls, run_dir: Path, d: dict) -> "RunRecord":
        return cls(
            run_dir=run_dir,
            run_id=d["run_id"],
            started_at=d["started_at"],
            finished_at=d.get("finished_at"),
            stage=d.get("stage"),
            outcome=Outcome(d["outcome"]) if d.get("outcome") else None,
            paper=Paper(**d["paper"]),
            policy=Policy(**d["policy"]),
            budget=Budget(**d["budget"]),
            caps=Caps(**d["caps"]),
            counters=Counters(**d["counters"]),
            error=RunError(**d["error"]) if d.get("error") else None,
            scope_manifest_sha256=d.get("scope_manifest_sha256"),
            workspace_sha256=d.get("workspace_sha256"),
        )

    def save(self) -> None:
        """Atomic write: a crash mid-write never leaves a torn run.json."""
        target = self.run_dir / RUN_JSON
        tmp = self.run_dir / (RUN_JSON + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, target)

    @classmethod
    def load(cls, run_dir: Path) -> "RunRecord":
        path = run_dir / RUN_JSON
        if not path.exists():
            raise FileNotFoundError(f"no {RUN_JSON} in {run_dir}")
        return cls.from_dict(run_dir, json.loads(path.read_text(encoding="utf-8")))


def create_run(runs_root: Path, today: date, caps: Caps, limit_usd: float) -> RunRecord:
    """Create runs/YYYY-MM-DD (or -2, -3, ... if that day already has a run) and write run.json."""
    base = today.isoformat()
    run_id = base
    n = 1
    while (runs_root / run_id).exists():
        n += 1
        run_id = f"{base}-{n}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    record = RunRecord(
        run_dir=run_dir,
        run_id=run_id,
        started_at=utcnow(),
        caps=caps,
        budget=Budget(limit_usd=limit_usd),
    )
    record.save()
    return record
