from pathlib import Path

import httpx
import pytest

from paper2code.arxiv.feed import RSS_URL, fetch_daily, parse_rss
from paper2code.arxiv.http import ArxivUnavailable, PoliteClient
from paper2code.arxiv.models import ArxivPaper, read_papers, write_papers

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _mock_http(routes, statuses=None):
    """routes: url -> bytes. statuses: list of status codes to serve in order for every request (default 200s)."""
    served = []

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(str(request.url))
        status = statuses.pop(0) if statuses else 200
        body = routes.get(str(request.url).split("?")[0], b"")
        return httpx.Response(status, content=body, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PoliteClient(client=client, min_interval_s=0.0, sleep=lambda s: None), served


def test_parse_rss_extracts_fields():
    papers = parse_rss((FIX / "rss_cs_LG.xml").read_bytes())
    assert [p.arxiv_id for p in papers] == ["2610.03769", "2610.03800", "2509.00001"]
    first = papers[0]
    assert first.version == 1
    assert first.title == "Bayes-Sufficient Compression Is Not Enough"
    assert first.abstract.startswith("Multi-agent LLM systems")
    assert "communication helps." in first.abstract
    assert first.authors == ["Ada Lovelace", "Charles Babbage"]
    assert first.categories == ["cs.LG", "cs.IT"]
    assert first.primary_category == "cs.LG"
    assert first.announce_type == "new"
    assert first.url == "https://arxiv.org/abs/2610.03769"
    assert first.published == "Tue, 06 Oct 2026 00:00:00 -0400"
    assert papers[2].version == 3
    assert papers[2].announce_type == "replace"


def test_fetch_daily_dedupes_across_feeds_and_skips_replacements():
    http, served = _mock_http({
        RSS_URL.format(category="cs.LG"): (FIX / "rss_cs_LG.xml").read_bytes(),
        RSS_URL.format(category="stat.ML"): (FIX / "rss_stat_ML.xml").read_bytes(),
    })
    papers = fetch_daily(["cs.LG", "stat.ML"], http)
    assert [p.arxiv_id for p in papers] == ["2610.03769", "2610.03800", "2610.03727"]
    assert served == [RSS_URL.format(category="cs.LG"), RSS_URL.format(category="stat.ML")]
    assert all(p.announce_type in ("new", "cross") for p in papers)


def test_papers_jsonl_roundtrip(tmp_path):
    papers = parse_rss((FIX / "rss_cs_LG.xml").read_bytes())
    path = tmp_path / "papers.jsonl"
    write_papers(path, papers)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3
    assert read_papers(path) == papers
    assert isinstance(read_papers(path)[0], ArxivPaper)


def test_polite_client_backs_off_on_503_then_succeeds():
    sleeps = []
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] < 3 else 200, content=b"ok", request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=sleeps.append)
    resp = client.get("https://example.invalid/x")
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert sleeps == [5.0, 10.0]


def test_polite_client_gives_up_after_max_attempts():
    sleeps = []
    client = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, content=b"Rate exceeded.", request=r))),
        min_interval_s=0.0, max_attempts=3, sleep=sleeps.append,
    )
    with pytest.raises(ArxivUnavailable, match="429"):
        client.get("https://example.invalid/x")
    assert sleeps == [5.0, 10.0]


def test_polite_client_returns_non_retryable_statuses():
    client = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, request=r))),
        min_interval_s=0.0, sleep=lambda s: None,
    )
    assert client.get("https://example.invalid/missing").status_code == 404


def test_polite_client_spaces_requests():
    sleeps = []
    client = PoliteClient(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, request=r))),
        min_interval_s=3.0, sleep=sleeps.append,
    )
    client.get("https://example.invalid/a")
    client.get("https://example.invalid/b")
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 3.0


def test_polite_client_sends_contact_user_agent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None, contact="me@example.org")
    client.get("https://example.invalid/a")
    assert seen["ua"].startswith("paper2code/")
    assert "me@example.org" in seen["ua"]


def test_polite_client_retries_transport_errors():
    sleeps = []
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, content=b"ok", request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=sleeps.append)
    assert client.get("https://example.invalid/x").status_code == 200
    assert calls["n"] == 3
    assert sleeps == [5.0, 10.0]


def test_polite_client_gives_up_on_persistent_transport_error():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    client = PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, max_attempts=2, sleep=lambda s: None)
    with pytest.raises(ArxivUnavailable, match="ConnectError"):
        client.get("https://example.invalid/x")
