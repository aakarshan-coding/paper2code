"""Paper metadata as fetched from arXiv, and its JSON-lines persistence."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ArxivPaper:
    arxiv_id: str  # without version, e.g. "2610.03769"
    version: int
    title: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    primary_category: str = ""
    announce_type: str = ""  # new | cross | replace | replace-cross | manual
    published: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ArxivPaper":
        return cls(**d)


def write_papers(path: Path, papers: list[ArxivPaper]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for p in papers:
            fh.write(json.dumps(p.to_dict()) + "\n")


def read_papers(path: Path) -> list[ArxivPaper]:
    if not path.exists():
        return []
    return [ArxivPaper.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
