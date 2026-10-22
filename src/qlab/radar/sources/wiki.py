"""Wikipedia page views: daily views of an article by people, from the Wikimedia REST API, since 2015-07-01.

Views by bots and automated agents are excluded (agent ``user``). Wikimedia asks clients to identify themselves with
a contact address in the user agent; the lab's names the project's public repository.

A day's views appear after the day ends: on 2026-10-22 the views of 2026-10-21 were served at 11:18 UTC. Rows are
stamped as published 24 hours after their day ends, or at the fetch if that came sooner. Every fetch asks again for
the last few days, since a late day can appear in a later fetch.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

import pandas as pd

from .. import store
from . import Log, published, summary

URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/all-access/user/{article}/daily/"
       "{start}/{end}")
FIRST = pd.Timestamp("2015-07-01", tz="UTC")
LAG = pd.Timedelta(hours=24)
REFETCH_DAYS = 5
SOURCE = "wiki"


def name_of(item: dict[str, Any]) -> str:
    """The series name of a watched article.

    Args:
        item: A ``wiki`` watchlist entry.

    Returns:
        A slug of the article title (and the project, when not English Wikipedia).
    """
    project = item.get("project", "en.wikipedia")
    return store.slug(item["article"] if project == "en.wikipedia" else f"{project}_{item['article']}")


def parse(payload: bytes, fetched: pd.Timestamp) -> pd.DataFrame:
    """Daily views from one API answer.

    Args:
        payload: The JSON body.
        fetched: When it was fetched (tz-aware UTC).

    Returns:
        Rows ``ts, views, published_at, fetched_at, partial``; empty when the answer has no items.
    """
    items = json.loads(payload).get("items") or []
    if not items:
        return pd.DataFrame(columns=["ts", "views", *store.BASE_COLUMNS[1:]])
    ts = pd.to_datetime([i["timestamp"][:8] for i in items], format="%Y%m%d", utc=True)
    df = pd.DataFrame({"ts": ts, "views": [float(i["views"]) for i in items]})
    df["published_at"] = published(df["ts"] + pd.Timedelta(days=1), LAG, fetched)
    df["fetched_at"] = fetched
    df["partial"] = False
    return df


def collect(items: list[dict[str, Any]], fetcher: store.Fetcher, now: datetime, deep: bool = False,
            log: Log | None = None) -> dict[str, Any]:
    """Fetch new days of views for every watched article.

    Args:
        items: ``wiki`` watchlist entries (``article``, ``label``, ``category``, optional ``project``).
        fetcher: The radar's HTTP fetcher.
        now: Current time (tz-aware).
        deep: True fetches each article's whole history again.
        log: Receives one line per article.

    Returns:
        The collector summary.
    """
    say = log or (lambda _m: None)
    t = pd.Timestamp(now).tz_convert("UTC")
    end = (t.normalize() - pd.Timedelta(days=1))
    n_series = n_rows = 0
    errors: list[str] = []
    for item in items:
        name = name_of(item)
        try:
            stored = store.series_path(SOURCE, name).exists()
            start = FIRST if deep or not stored else max(FIRST, store.read(SOURCE, name)["ts"].max()
                                                         - pd.Timedelta(days=REFETCH_DAYS))
            url = URL.format(project=item.get("project", "en.wikipedia"), article=quote(item["article"], safe=""),
                             start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
            body = fetcher.get(url, cache=store.raw_path(SOURCE, f"{name}_{start:%Y%m%d}_{end:%Y%m%d}.json"),
                               ttl=6 * 3600, allow_404=True)
            df = parse(body, t) if body is not None else parse(b"{}", t)
            if df.empty:
                errors.append(f"{item['article']}: no views returned (renamed or not found?)")
                continue
            row = store.upsert(SOURCE, name, df, {"label": item.get("label", item["article"]),
                                                  "category": item.get("category", ""), "unit": "views/day",
                                                  "frequency": "1d"})
            n_series, n_rows = n_series + 1, n_rows + row["added"]
            say(f"wiki {item['article']}: {row['n_rows']} days through {str(row['last_ts'])[:10]}, +{row['added']}")
        except Exception as err:  # one article's failure must not stop the others
            errors.append(f"{item['article']}: {err}")
    return summary(SOURCE, n_series, n_rows, errors, now)
