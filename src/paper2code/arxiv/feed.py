"""arXiv's daily per-category RSS feeds: the day's announcements with abstracts inline."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from paper2code.arxiv.http import PoliteClient
from paper2code.arxiv.models import ArxivPaper

RSS_URL = "https://rss.arxiv.org/rss/{category}"
NEW_TYPES = ("new", "cross")
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"
_VERSION_RE = re.compile(r"^(?P<id>.+?)v(?P<v>\d+)$")


def _split_version(versioned: str) -> tuple[str, int]:
    m = _VERSION_RE.match(versioned)
    if not m:
        return versioned, 1
    return m.group("id"), int(m.group("v"))


def _abstract_from_description(description: str) -> str:
    marker = "Abstract:"
    idx = description.find(marker)
    text = description[idx + len(marker):] if idx >= 0 else description
    return " ".join(text.split())


def parse_rss(xml_bytes: bytes) -> list[ArxivPaper]:
    root = ET.fromstring(xml_bytes)
    papers: list[ArxivPaper] = []
    for item in root.findall("./channel/item"):
        guid = item.findtext("guid") or ""
        arxiv_id, version = _split_version(guid.rsplit(":", 1)[-1])
        categories = [c.text.strip() for c in item.findall("category") if c.text]
        creators = item.findtext(_DC_NS + "creator") or ""
        papers.append(ArxivPaper(
            arxiv_id=arxiv_id,
            version=version,
            title=" ".join((item.findtext("title") or "").split()),
            abstract=_abstract_from_description(item.findtext("description") or ""),
            authors=[a.strip() for a in creators.split(",") if a.strip()],
            categories=categories,
            primary_category=categories[0] if categories else "",
            announce_type=(item.findtext(_ARXIV_NS + "announce_type") or "").strip(),
            published=(item.findtext("pubDate") or "").strip(),
            url=(item.findtext("link") or "").strip(),
        ))
    return papers


def fetch_daily(categories: list[str], http: PoliteClient) -> list[ArxivPaper]:
    """New submissions and cross-lists across the categories, each paper once."""
    seen: dict[str, ArxivPaper] = {}
    for category in categories:
        resp = http.get(RSS_URL.format(category=category))
        resp.raise_for_status()
        for paper in parse_rss(resp.content):
            if paper.announce_type in NEW_TYPES and paper.arxiv_id not in seen:
                seen[paper.arxiv_id] = paper
    return list(seen.values())
