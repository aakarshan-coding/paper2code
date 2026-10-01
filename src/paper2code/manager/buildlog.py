"""build.log: JSON lines, written only by the manager."""
from __future__ import annotations

import json
from pathlib import Path

from paper2code.manager.record import utcnow


class BuildLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: dict) -> None:
        row = {"ts": utcnow(), **event}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def events(self, kind: str) -> list[dict]:
        return [row for row in self.read() if row.get("event") == kind]
