"""The paper feed: new quantitative-finance papers from the arXiv API, stored once each.

Design:

- **Source.** arXiv's Atom API (``export.arxiv.org/api/query``), newest first, in the trading, portfolio,
  statistical and computational finance categories, plus optional keyword queries. arXiv asks clients to wait
  three seconds between calls and to keep pages modest; the feed does both.
- **Stored once.** A paper is keyed by its arXiv id without the version; a later version updates the title,
  abstract and dates but keeps its triage history. Fetching again is safe.
- **Untrusted text.** Titles and abstracts are stored as they arrive and only ever shown or handed to the triage
  model as data (see ``inbox.llm``).
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx

from .. import audit, db
from ..timeutil import naive_utc

API = "https://export.arxiv.org/api/query"
CATEGORIES = ["q-fin.TR", "q-fin.PM", "q-fin.ST", "q-fin.CP"]
NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
PAUSE = 3.0  # seconds between API calls, as arXiv asks


class FeedError(RuntimeError):
    """The arXiv API couldn't be reached or returned something unreadable."""


def parse_feed(xml: bytes | str) -> list[dict[str, Any]]:
    """Papers in one page of the arXiv Atom API.

    Args:
        xml: The response body.

    Returns:
        One dict per entry: ``arxiv_id`` (no version), ``version``, ``title``, ``abstract``, ``authors`` (list),
        ``categories`` (list), ``primary``, ``published``, ``updated`` (naive UTC datetimes), ``url`` and
        ``pdf_url``. Whitespace in titles and abstracts is collapsed.

    Raises:
        FeedError: The body isn't an Atom feed.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as err:
        raise FeedError(f"unreadable arXiv answer ({err})") from None
    out = []
    for e in root.findall("a:entry", NS):
        raw_id = (e.findtext("a:id", default="", namespaces=NS) or "").rsplit("/abs/", 1)[-1]
        m = re.match(r"(.+?)(v\d+)?$", raw_id)
        if not raw_id or not m:
            continue
        links = {link.get("title") or link.get("rel"): link.get("href") for link in e.findall("a:link", NS)}
        primary = e.find("arxiv:primary_category", NS)
        out.append({
            "arxiv_id": m.group(1),
            "version": int((m.group(2) or "v1")[1:]),
            "title": " ".join((e.findtext("a:title", default="", namespaces=NS) or "").split()),
            "abstract": " ".join((e.findtext("a:summary", default="", namespaces=NS) or "").split()),
            "authors": [a.findtext("a:name", default="", namespaces=NS) for a in e.findall("a:author", NS)],
            "categories": [c.get("term") for c in e.findall("a:category", NS)],
            "primary": primary.get("term") if primary is not None else "",
            "published": _time(e.findtext("a:published", default="", namespaces=NS)),
            "updated": _time(e.findtext("a:updated", default="", namespaces=NS)),
            "url": links.get("alternate") or f"https://arxiv.org/abs/{m.group(1)}",
            "pdf_url": links.get("pdf") or f"https://arxiv.org/pdf/{m.group(1)}",
        })
    return out


def _time(text: str) -> datetime | None:
    """An arXiv timestamp as naive UTC.

    Args:
        text: ISO time such as ``2026-10-16T16:55:06Z``.

    Returns:
        The time, or None when empty.
    """
    if not text:
        return None
    return naive_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))


def query(categories: list[str] | None = None, keywords: str = "") -> str:
    """The ``search_query`` for the categories, optionally narrowed by keywords.

    Args:
        categories: arXiv categories; None uses ``CATEGORIES``.
        keywords: Words that must appear in the title or abstract (all of them), e.g. ``"momentum crypto"``.

    Returns:
        A query such as ``(cat:q-fin.TR OR cat:q-fin.PM) AND all:momentum``.
    """
    cats = " OR ".join(f"cat:{c}" for c in categories or CATEGORIES)
    words = " AND ".join(f"all:{w}" for w in keywords.split() if w.isalnum())
    return f"({cats})" + (f" AND {words}" if words else "")


def fetch(max_papers: int = 200, page: int = 100, keywords: str = "", client: httpx.Client | None = None,
          sleep=time.sleep, log=None) -> list[dict[str, Any]]:
    """The newest papers from arXiv, page by page, pausing between calls.

    Args:
        max_papers: Most papers to fetch.
        page: Papers per API call (arXiv suggests at most a few hundred).
        keywords: Optional keyword filter (see ``query``).
        client: HTTP client (tests pass a mock).
        sleep: Pause function (tests pass a no-op).
        log: Receives progress lines.

    Returns:
        Parsed entries, newest first.

    Raises:
        FeedError: The API failed.
    """
    say = log or (lambda _msg: None)
    http = client or httpx.Client(timeout=60, follow_redirects=True,
                                  headers={"User-Agent": "quant-lab/0.1 (personal research inbox)"})
    out: list[dict[str, Any]] = []
    for start in range(0, max_papers, page):
        if start:
            sleep(PAUSE)
        params = {"search_query": query(keywords=keywords), "sortBy": "submittedDate", "sortOrder": "descending",
                  "start": start, "max_results": min(page, max_papers - start)}
        try:
            res = http.get(f"{API}?{urlencode(params)}")
        except httpx.HTTPError as err:
            raise FeedError(f"arXiv API failed ({type(err).__name__})") from None
        if res.status_code != 200:
            raise FeedError(f"arXiv API answered HTTP {res.status_code}")
        batch = parse_feed(res.content)
        say(f"arXiv: {len(batch)} papers from {start}")
        out += batch
        if len(batch) < params["max_results"]:
            break
    return out


def store(papers: list[dict[str, Any]], now: datetime | None = None) -> dict[str, int]:
    """Insert new papers and refresh ones that have a newer version.

    Args:
        papers: Parsed entries.
        now: Time of the fetch; None means now.

    Returns:
        ``{"new": n, "updated": n, "seen": n}``.
    """
    when = naive_utc(now)
    counts = {"new": 0, "updated": 0, "seen": 0}
    with db.connect() as con:
        for p in papers:
            row = con.execute("SELECT version FROM papers WHERE arxiv_id = ?", [p["arxiv_id"]]).fetchone()
            values = [p["version"], p["title"], p["abstract"], db.dumps(p["authors"]), db.dumps(p["categories"]),
                      p["primary"], p["published"], p["updated"], p["url"], p["pdf_url"]]
            if row is None:
                con.execute("""INSERT INTO papers (arxiv_id, version, title, abstract, authors, categories, primary_cat,
                               published, updated, url, pdf_url, fetched_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            [p["arxiv_id"], *values, when])
                counts["new"] += 1
            elif p["version"] > row[0]:
                con.execute("""UPDATE papers SET version = ?, title = ?, abstract = ?, authors = ?, categories = ?,
                               primary_cat = ?, published = ?, updated = ?, url = ?, pdf_url = ? WHERE arxiv_id = ?""",
                            [*values, p["arxiv_id"]])
                counts["updated"] += 1
            else:
                counts["seen"] += 1
    return counts


def fetch_and_store(max_papers: int = 200, keywords: str = "", actor: str = "user", log=None) -> dict[str, int]:
    """Fetch the newest papers and store them.

    Args:
        max_papers: Most papers to fetch.
        keywords: Optional keyword filter.
        actor: Who ran it, for the audit log.
        log: Receives progress lines.

    Returns:
        Counts of new, updated and already-seen papers.

    Raises:
        FeedError: The API failed.
    """
    counts = store(fetch(max_papers, keywords=keywords, log=log))
    audit.log("inbox.fetch", target="arxiv", actor=actor, details={**counts, "keywords": keywords})
    return counts
