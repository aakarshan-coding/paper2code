"""Live smoke test against arXiv. Opt in with PAPER2CODE_LIVE=1; it makes real HTTP requests."""
import os

import pytest

from paper2code.arxiv.api import fetch_by_id
from paper2code.arxiv.feed import fetch_daily
from paper2code.arxiv.fulltext import fetch_fulltext
from paper2code.arxiv.http import PoliteClient

pytestmark = pytest.mark.skipif(os.environ.get("PAPER2CODE_LIVE") != "1", reason="set PAPER2CODE_LIVE=1 to hit arXiv")


def test_live_daily_feed_has_papers_with_abstracts():
    papers = fetch_daily(["stat.ML"], PoliteClient(contact="sriramsattiraju@utexas.edu"))
    assert len(papers) > 0
    assert all(p.arxiv_id and p.title and p.abstract for p in papers)
    assert all(p.announce_type in ("new", "cross") for p in papers)


def test_live_lookup_and_fulltext():
    http = PoliteClient(contact="sriramsattiraju@utexas.edu")
    paper = fetch_by_id("2610.03769", http)
    assert paper.title
    ft = fetch_fulltext(paper.arxiv_id, http, max_chars=5000)
    assert ft.chars == 5000 and ft.source in ("html", "pdf")
