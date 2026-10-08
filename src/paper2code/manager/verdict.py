"""The inspector's verdict. The outcome rule is mechanical; inspector judgment only produces flags."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from paper2code.manager.outcomes import Outcome
from paper2code.sandbox.runner import TestRunResult

VERDICT_JSON = "verdict.json"


@dataclass(frozen=True)
class Flag:
    kind: str
    file: str
    line: int | None
    note: str
    source: str = "inspector"  # inspector | build_log | workspace_scan


def decide(integrity_mismatches: Sequence[str], hidden: TestRunResult, flags: Sequence[Flag]) -> Outcome:
    """Spec section 10.4, verbatim order of precedence."""
    if integrity_mismatches:
        return Outcome.TESTS_TAMPERED
    if not hidden.all_passed:
        return Outcome.HIDDEN_FAILED
    if not flags:
        return Outcome.COMPLETED
    return Outcome.COMPLETED_SUSPICIOUS


@dataclass
class Verdict:
    outcome: Outcome
    integrity_mismatches: list[str] = field(default_factory=list)
    hidden_passed: list[str] = field(default_factory=list)
    hidden_failed: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    summary: str = ""
    confidence: float | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d

    def write(self, run_dir: Path) -> Path:
        path = run_dir / VERDICT_JSON
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, run_dir: Path) -> "Verdict":
        d = json.loads((run_dir / VERDICT_JSON).read_text(encoding="utf-8"))
        return cls(
            outcome=Outcome(d["outcome"]),
            integrity_mismatches=list(d["integrity_mismatches"]),
            hidden_passed=list(d["hidden_passed"]),
            hidden_failed=list(d["hidden_failed"]),
            flags=[Flag(**{"source": "inspector", **f}) for f in d["flags"]],  # older files have no `source`
            summary=d["summary"],
            confidence=d["confidence"],
        )
