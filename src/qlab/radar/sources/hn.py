"""Hacker News: stories per day whose title has a watched keyword, their points and comments, from Algolia's API.

- **Exact words.** Algolia matches word prefixes and typos by default, so "AI" would also count "air"; the radar asks
  for exact words in titles only (``queryType=prefixNone``, ``typoTolerance=false``,
  ``restrictSearchableAttributes=title``).
- **Counts settle at the day's end; points don't.** A story keeps collecting points for a day or two, so each pass
  asks again for the last few days and the newest fetch wins. Rows of a day still running are marked ``partial``.
- **Paging.** One request returns at most 1,000 stories; a span with more is split in halves until each fits.
- Titles are stored as headlines with the lexicon's score (untrusted text, kept as data).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from .. import sentiment, store
from . import Log, published, summary

URL = "https://hn.algolia.com/api/v1/search_by_date"
LAG = pd.Timedelta(hours=1)
BACKFILL_DAYS = 365
REFETCH_DAYS = 3
MAX_HITS = 1000
SOURCE = "hn"


def _query(fetcher: store.Fetcher, keyword: str, start: int, end: int, final: bool) -> list[dict[str, Any]]:
    """Stories with the keyword in their title created in ``[start, end)``.

    Args:
        fetcher: The radar's fetcher.
        keyword: Word to match.
        start: First Unix second.
        end: Unix second after the last.
        final: Whether the span is old enough to cache for good (its points have settled).

    Returns:
        Hits with ``objectID``, ``title``, ``url``, ``points``, ``num_comments`` and ``created_at_i``.
    """
    params = {"query": keyword, "tags": "story", "hitsPerPage": MAX_HITS,
              "numericFilters": f"created_at_i>={start},created_at_i<{end}",
              "restrictSearchableAttributes": "title", "queryType": "prefixNone", "typoTolerance": "false",
              "attributesToRetrieve": "title,url,points,num_comments,created_at_i", "attributesToHighlight": ""}
    cache = store.raw_path(SOURCE, f"{store.slug(keyword)}_{start}_{end}.json")
    body = json.loads(fetcher.get(URL, params, cache=cache, ttl=None if final else 600) or b"{}")
    hits = body.get("hits") or []
    if int(body.get("nbHits") or 0) > len(hits) and end - start > 3600:
        mid = (start + end) // 2
        return _query(fetcher, keyword, start, mid, final) + _query(fetcher, keyword, mid, end, final)
    return hits


def daily(hits: list[dict[str, Any]], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Stories, points and comments per UTC day.

    Args:
        hits: Algolia hits.
        start: First day asked for (UTC midnight).
        end: Day after the last one asked for.

    Returns:
        One row per day in ``[start, end)``, days without stories as zeros: ``ts, stories, points, comments``.
    """
    days = pd.date_range(start, end - pd.Timedelta(days=1), freq="D", tz="UTC")
    if hits:
        df = pd.DataFrame({"ts": pd.to_datetime([h["created_at_i"] for h in hits], unit="s", utc=True).normalize(),
                           "points": [float(h.get("points") or 0) for h in hits],
                           "comments": [float(h.get("num_comments") or 0) for h in hits]})
        g = df.groupby("ts").agg(stories=("points", "size"), points=("points", "sum"), comments=("comments", "sum"))
    else:
        g = pd.DataFrame(columns=["stories", "points", "comments"], dtype=float)
    g = g.reindex(days, fill_value=0.0).astype(float)
    g.index.name = "ts"
    return g.reset_index()


def collect(items: list[dict[str, Any]], fetcher: store.Fetcher, now: datetime, deep: bool = False,
            log: Log | None = None) -> dict[str, Any]:
    """Update every keyword's daily counts and store the titles of recent stories.

    Args:
        items: ``hn`` watchlist entries (``keyword``, ``label``, ``category``).
        fetcher: The radar's fetcher.
        now: Current time (tz-aware).
        deep: True also refetches the last week (points settle); the first run of a keyword backfills a year.
        log: Receives one line per keyword.

    Returns:
        The collector summary.
    """
    say = log or (lambda _m: None)
    t = pd.Timestamp(now).tz_convert("UTC")
    today = t.normalize()
    end = today + pd.Timedelta(days=1)
    n_series = n_rows = 0
    errors: list[str] = []
    heads: list[dict[str, Any]] = []
    words = sentiment.lexicon()
    for item in items:
        name = store.slug(item["keyword"])
        try:
            stored = store.series_path(SOURCE, name).exists()
            back = BACKFILL_DAYS if not stored else (7 if deep else REFETCH_DAYS)
            start = today - pd.Timedelta(days=back)
            hits: list[dict[str, Any]] = []
            # Settled weeks are cached for good; the last two days are asked again each pass.
            cut = today - pd.Timedelta(days=2)
            chunk = start
            while chunk < cut:
                nxt = min(chunk + pd.Timedelta(days=7), cut)
                hits += _query(fetcher, item["keyword"], int(chunk.timestamp()), int(nxt.timestamp()), True)
                chunk = nxt
            hits += _query(fetcher, item["keyword"], int(max(cut, start).timestamp()), int(end.timestamp()), False)
            df = daily(hits, start, end)
            df["partial"] = df["ts"] + pd.Timedelta(days=1) > t
            df["published_at"] = published(df["ts"] + pd.Timedelta(days=1), LAG, t)
            df.loc[df["partial"], "published_at"] = t
            df["fetched_at"] = t
            row = store.upsert(SOURCE, name, df, {"label": item.get("label", item["keyword"]),
                                                  "category": item.get("category", ""), "unit": "stories/day",
                                                  "frequency": "1d"})
            n_series, n_rows = n_series + 1, n_rows + row["added"]
            say(f"hn {item['keyword']}: {int(df['stories'].iloc[-1])} stories today, {row['n_rows']} days stored")
            recent = [h for h in hits if h.get("created_at_i", 0) >= (t - pd.Timedelta(hours=48)).timestamp()]
            for h in recent:
                title = sentiment.clean(h.get("title"))
                if title:
                    heads.append({"source": SOURCE, "item_id": str(h.get("objectID")), "topic": name, "title": title,
                                  "url": str(h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                                             )[:500], "domain": "news.ycombinator.com", "language": "English",
                                  "published_at": pd.Timestamp(h["created_at_i"], unit="s", tz="UTC"),
                                  "fetched_at": t, "points": int(h.get("points") or 0),
                                  **sentiment.score(title, words)})
        except Exception as err:  # one keyword's failure must not stop the others
            errors.append(f"{item['keyword']}: {err}")
    n_heads = store.add_headlines(heads)
    return summary(SOURCE, n_series, n_rows, errors, now, f"{n_heads} headlines")
