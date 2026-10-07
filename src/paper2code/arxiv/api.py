"""Single-paper lookup through the arXiv export API (Atom)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from paper2code.arxiv.feed import _split_version
from paper2code.arxiv.http import PoliteClient
from paper2code.arxiv.models import ArxivPaper

API_URL = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def fetch_by_id(arxiv_id: str, http: PoliteClient) -> ArxivPaper:
    resp = http.get(API_URL, params={"id_list": arxiv_id, "max_results": 1})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    entry = root.find("a:entry", _NS)
    if entry is None:
        raise LookupError(f"arXiv has no entry for {arxiv_id}")
    versioned = (entry.findtext("a:id", default="", namespaces=_NS) or "").rsplit("/", 1)[-1]
    bare_id, version = _split_version(versioned)
    categories = [c.get("term", "") for c in entry.findall("a:category", _NS)]
    primary = entry.find("arxiv:primary_category", _NS)
    alternate = next((l.get("href", "") for l in entry.findall("a:link", _NS) if l.get("rel") == "alternate"), "")
    return ArxivPaper(
        arxiv_id=bare_id,
        version=version,
        title=" ".join((entry.findtext("a:title", default="", namespaces=_NS) or "").split()),
        abstract=" ".join((entry.findtext("a:summary", default="", namespaces=_NS) or "").split()),
        authors=[(a.findtext("a:name", default="", namespaces=_NS) or "").strip() for a in entry.findall("a:author", _NS)],
        categories=categories,
        primary_category=primary.get("term", "") if primary is not None else (categories[0] if categories else ""),
        announce_type="manual",
        published=(entry.findtext("a:published", default="", namespaces=_NS) or "").strip(),
        url=alternate,
    )
