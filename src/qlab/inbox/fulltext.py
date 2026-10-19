"""The deep read: a paper's full text from arXiv's HTML version, cut down to the passages a spec needs.

Design (decision 0010):

- Triage reads only abstracts. Drafting a spec needs more: abstracts rarely state the sample, the rebalance or the
  Sharpe ratio. arXiv renders most papers submitted since late 2023 as HTML (``arxiv.org/html/<id>``), which the
  standard library can parse; older papers have no HTML version and keep the abstract-only spec.
- **Excerpts, not the whole paper.** A local model's context is a few thousand words, so the text is cut to the
  opening (title, abstract, introduction) and the passages that mention performance, samples, costs or the
  rule's parameters, in document order. Formulas are kept as their LaTeX, tables as ``|``-separated rows, and the
  bibliography is dropped.
- The text is untrusted, like the abstract: it goes to the model between markers and is never executed. It is
  cached under ``raw/arxiv`` so a paper is fetched once.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import httpx

from .. import config

HTML_URL = "https://arxiv.org/html/{arxiv_id}"
USER_AGENT = "quant-lab/0.1 (personal research inbox)"
BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "caption", "figcaption", "dt", "dd"}
SKIP = {"script", "style", "head", "nav", "footer", "button", "svg"}
KEYWORDS = re.compile(
    r"sharpe|sortino|cagr|annuali[sz]ed|per annum|excess return|volatility|drawdown|turnover|transaction cost|"
    r"basis points|\bbps\b|slippage|out-of-sample|in-sample|sample period|rebalanc|look-?back|holding period|"
    r"\b(?:19|20)\d\d\b|monthly|weekly|daily|moving average|momentum|signal|t-stat", re.IGNORECASE)


class FulltextError(Exception):
    """arXiv failed while fetching a paper's HTML version."""


class _Text(HTMLParser):
    """Collects an arXiv HTML page's article (not the site's menus) as text blocks: paragraphs, headings, list items,
    table rows and captions."""

    def __init__(self) -> None:
        """Start empty."""
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self._math = 0
        self._biblio = 0
        self._depth = 0
        self._article = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track skipped regions, keep a formula's LaTeX, and separate table cells.

        Args:
            tag: Tag name.
            attrs: Attributes.
        """
        a = dict(attrs)
        if tag == "article":
            self._article += 1
        if tag == "section" and self._biblio:
            self._depth += 1
        elif tag == "section" and "ltx_bibliography" in (a.get("class") or ""):
            self._biblio, self._depth = 1, 1
        if tag in SKIP:
            self._skip += 1
        elif tag == "math":
            self._math += 1
            if self._math == 1 and a.get("alttext"):
                self._buf.append(f" {a['alttext']} ")
        elif tag in ("td", "th") and self._buf:
            self._buf.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        """Close skipped regions and flush a finished block.

        Args:
            tag: Tag name.
        """
        if tag == "article":
            self._article = max(0, self._article - 1)
        if tag == "section" and self._biblio:
            self._depth -= 1
            if self._depth == 0:
                self._biblio = 0
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "math":
            self._math = max(0, self._math - 1)
        elif tag in BLOCKS:
            self._flush()

    def handle_data(self, data: str) -> None:
        """Keep the article's visible text outside formulas, skipped regions and the bibliography.

        Args:
            data: Text.
        """
        if self._article and not (self._skip or self._math or self._biblio):
            self._buf.append(data)

    def _flush(self) -> None:
        """End the current block."""
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if text and not self._biblio:
            self.blocks.append(text)

    def close(self) -> None:
        """Flush what is left."""
        super().close()
        self._flush()


def parse_html(html: str) -> str:
    """An arXiv HTML page as plain text, one block per line.

    Args:
        html: The page.

    Returns:
        The text.
    """
    parser = _Text()
    parser.feed(html)
    parser.close()
    return "\n".join(parser.blocks)


def excerpt(text: str, limit: int = 12000, opening: int = 2500) -> str:
    """Cut a paper's text to its opening and the passages most about performance, samples, costs and parameters.

    Args:
        text: The paper, one block per line.
        limit: Most characters.
        opening: Characters always kept from the start (title, abstract, introduction).

    Returns:
        The excerpt, in document order, with ``[...]`` where blocks were dropped.
    """
    blocks = text.split("\n")
    keep: set[int] = set()
    used = 0
    for i, b in enumerate(blocks):
        if used >= opening:
            break
        keep.add(i)
        used += len(b) + 1
    ranked = sorted((i for i in range(len(blocks)) if i not in keep),
                    key=lambda i: (-len(KEYWORDS.findall(blocks[i])) / (1 + len(blocks[i]) / 400), i))
    for i in ranked:
        if not KEYWORDS.search(blocks[i]) or used + len(blocks[i]) + 1 > limit:
            continue
        keep.add(i)
        used += len(blocks[i]) + 1
    out, last = [], -1
    for i in sorted(keep):
        if i != last + 1:
            out.append("[...]")
        out.append(blocks[i])
        last = i
    return "\n".join(out)


def cache_path(arxiv_id: str) -> Path:
    """Where a paper's text is cached.

    Args:
        arxiv_id: Paper id.

    Returns:
        ``<raw>/arxiv/<id>.txt``.
    """
    return config.raw_dir() / "arxiv" / f"{arxiv_id.replace('/', '_')}.txt"


def read(arxiv_id: str, client: httpx.Client | None = None) -> str | None:
    """A paper's full text, from the cache or arXiv's HTML version.

    Args:
        arxiv_id: Paper id.
        client: HTTP client (tests pass a mock).

    Returns:
        The text, or None when arXiv has no HTML version of the paper.

    Raises:
        FulltextError: arXiv failed.
    """
    path = cache_path(arxiv_id)
    if path.exists():
        return path.read_text() or None
    http = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        res = http.get(HTML_URL.format(arxiv_id=arxiv_id))
    except httpx.HTTPError as err:
        raise FulltextError(f"arXiv HTML failed ({type(err).__name__})") from None
    if res.status_code == 404 or (res.status_code == 200 and "html" not in res.headers.get("content-type", "")):
        text = ""
    elif res.status_code != 200:
        raise FulltextError(f"arXiv HTML answered HTTP {res.status_code}")
    else:
        text = parse_html(res.text)
        text = text if len(text) > 2000 else ""  # a stub page ("no HTML for this paper") isn't the paper
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text or None


__all__ = ["FulltextError", "excerpt", "parse_html", "read"]
