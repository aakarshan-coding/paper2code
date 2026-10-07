"""Full text of a paper for scout pass two: arXiv's HTML rendering first, PDF as fallback."""
from __future__ import annotations

import io
from dataclasses import dataclass
from html.parser import HTMLParser

from pypdf import PdfReader

from paper2code.arxiv.http import PoliteClient

HTML_URL = "https://arxiv.org/html/{arxiv_id}"
PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"
_SKIP_TAGS = {"script", "style", "annotation", "annotation-xml"}


class FullTextUnavailable(Exception):
    """Neither the HTML rendering nor the PDF could be fetched."""


@dataclass(frozen=True)
class FullText:
    text: str
    source: str  # "html" | "pdf"
    truncated: bool
    chars: int


class _ArticleText(HTMLParser):
    """Collects text inside <article> (or the whole document if there is no article)."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.article_depth = 0
        self.skip_depth = 0
        self.saw_article = False
        self.body_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self.article_depth += 1
            self.saw_article = True
        if tag in _SKIP_TAGS:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "article" and self.article_depth:
            self.article_depth -= 1
        if tag in _SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.article_depth:
            self.parts.append(data)
        self.body_parts.append(data)

    def text(self) -> str:
        chosen = self.parts if self.saw_article else self.body_parts
        return " ".join(" ".join(chosen).split())


def html_to_text(html: str) -> str:
    parser = _ArticleText()
    parser.feed(html)
    return parser.text()


def pdf_to_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages).strip()


def _cut(text: str, max_chars: int, source: str) -> FullText:
    truncated = len(text) > max_chars
    text = text[:max_chars] if truncated else text
    return FullText(text=text, source=source, truncated=truncated, chars=len(text))


def fetch_fulltext(arxiv_id: str, http: PoliteClient, max_chars: int) -> FullText:
    resp = http.get(HTML_URL.format(arxiv_id=arxiv_id))
    if resp.status_code == 200 and resp.content:
        text = html_to_text(resp.text)
        if text:
            return _cut(text, max_chars, "html")
    resp = http.get(PDF_URL.format(arxiv_id=arxiv_id))
    if resp.status_code == 200 and resp.content:
        try:
            return _cut(pdf_to_text(resp.content), max_chars, "pdf")
        except Exception as exc:  # pypdf raises a zoo of exceptions on odd files
            raise FullTextUnavailable(f"{arxiv_id}: pdf parse failed: {exc}") from exc
    raise FullTextUnavailable(f"{arxiv_id}: html and pdf both unavailable")
