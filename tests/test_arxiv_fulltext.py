from pathlib import Path

import httpx
import pytest

from paper2code.arxiv.api import API_URL, fetch_by_id
from paper2code.arxiv.fulltext import FullTextUnavailable, fetch_fulltext, html_to_text, pdf_to_text
from paper2code.arxiv.http import PoliteClient

FIX = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _client(handler):
    return PoliteClient(client=httpx.Client(transport=httpx.MockTransport(handler)), min_interval_s=0.0, sleep=lambda s: None)


def _tiny_pdf() -> bytes:
    from io import BytesIO

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    # pypdf cannot author text simply; extract_text on a blank page returns "". The HTML route
    # covers real text extraction; this PDF exercises the fallback dispatch and the parser.
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_fetch_by_id_parses_atom_entry():
    def handler(request):
        assert str(request.url).startswith(API_URL)
        assert request.url.params["id_list"] == "2610.03769"
        return httpx.Response(200, content=(FIX / "api_by_id.xml").read_bytes(), request=request)

    paper = fetch_by_id("2610.03769", _client(handler))
    assert paper.arxiv_id == "2610.03769"
    assert paper.version == 1
    assert paper.title == "Bayes-Sufficient Compression Is Not Enough"
    assert paper.abstract.startswith("Multi-agent LLM systems")
    assert paper.authors == ["Ada Lovelace", "Charles Babbage"]
    assert paper.categories == ["cs.LG", "cs.IT"]
    assert paper.primary_category == "cs.LG"
    assert paper.announce_type == "manual"
    assert paper.url == "http://arxiv.org/abs/2610.03769v1"
    assert paper.published == "2026-10-05T17:59:59Z"


def test_fetch_by_id_raises_lookup_error_when_empty():
    empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>empty</title></feed>'
    with pytest.raises(LookupError):
        fetch_by_id("0000.00000", _client(lambda r: httpx.Response(200, content=empty, request=r)))


def test_html_to_text_keeps_article_drops_chrome():
    text = html_to_text((FIX / "paper.html").read_text(encoding="utf-8"))
    assert text.startswith("Bayes-Sufficient Compression Is Not Enough")
    assert "beats the baseline by 12 points on GSM8K" in text
    assert "x only." in text
    assert "Skip to main content" not in text
    assert "Copyright notice" not in text
    assert "window.x" not in text
    assert ".ltx_page" not in text
    assert "  " not in text


def test_html_to_text_without_article_uses_body():
    assert html_to_text("<html><body><p>Just a  body.</p></body></html>") == "Just a body."


def test_pdf_to_text_on_blank_page_is_empty():
    assert pdf_to_text(_tiny_pdf()) == ""


def test_fetch_fulltext_prefers_html_and_truncates():
    def handler(request):
        if "/html/" in str(request.url):
            return httpx.Response(200, content=(FIX / "paper.html").read_bytes(), request=request)
        raise AssertionError("pdf should not be requested when html exists")

    ft = fetch_fulltext("2610.03769", _client(handler), max_chars=60)
    assert ft.source == "html"
    assert ft.truncated is True
    assert ft.chars == 60
    assert ft.text == html_to_text((FIX / "paper.html").read_text(encoding="utf-8"))[:60]


def test_fetch_fulltext_falls_back_to_pdf():
    def handler(request):
        if "/html/" in str(request.url):
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=_tiny_pdf(), headers={"content-type": "application/pdf"}, request=request)

    ft = fetch_fulltext("2610.03769", _client(handler), max_chars=1000)
    assert ft.source == "pdf"
    assert ft.truncated is False


def test_fetch_fulltext_raises_when_both_fail():
    with pytest.raises(FullTextUnavailable):
        fetch_fulltext("2610.03769", _client(lambda r: httpx.Response(404, request=r)), max_chars=1000)
