"""GDELT 2.0 DOC API: daily news volume and average tone for each watched query, and recent headlines.

GDELT monitors online news worldwide every 15 minutes. For a query, ``timelinevolraw`` gives the number of matching
articles per period with ``norm``, the number of articles GDELT monitored in that period, so the share of coverage is
comparable across years in which GDELT's own coverage grew; ``timelinetone`` gives the average tone of the matching
articles (GDELT's dictionary-based score, roughly -10 to +10); ``artlist`` gives recent matching articles.

- **Point in time.** A day's values settle within about an hour of the day's end (GDELT updates every 15 minutes), so
  rows are stamped as published one hour after their day ends. GDELT recomputes timelines from its current index:
  values fetched today for an old day are today's view of that day, not what a reader saw then. The radar keeps
  ``fetched_at`` so the difference can be measured where it matters.
- **Rate limits.** GDELT asks for at most one request every five seconds and, from the owner's network, refused far
  more than that (decision 0016). The collector asks one request at a time, waits long after a refusal, and stops for
  the pass after two requests in a row fail; the next pass resumes where it stopped (finished chunks are cached).
- **Only in the deep pass** (once a day): GDELT is too strict for the 15-minute pass.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from ...data.http import SourceError
from .. import sentiment, store
from . import Log, published, summary

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
FIRST = pd.Timestamp("2017-01-01", tz="UTC")
LAG = pd.Timedelta(hours=1)
REFETCH_DAYS = 10
MAX_FAILURES = 2  # failed requests in a row before GDELT is left alone for the pass
HEADLINES = 50
SOURCE = "gdelt"
LANGUAGE = " sourcelang:english"


def _when(text: str) -> pd.Timestamp:
    """GDELT's compact timestamp as a UTC time.

    Args:
        text: e.g. ``20261015T120000Z`` or ``20261015120000``.

    Returns:
        Tz-aware UTC timestamp.
    """
    return pd.Timestamp(text.replace("T", "").replace("Z", "")[:14].ljust(14, "0"), tz="UTC")


def parse_timeline(payload: bytes) -> pd.DataFrame:
    """Periods of a timeline answer.

    Args:
        payload: JSON body of a ``timelinevolraw`` or ``timelinetone`` request.

    Returns:
        Rows ``ts`` (period start), ``value`` and ``norm`` (NaN for tone); empty when GDELT found nothing.

    Raises:
        SourceError: The body isn't JSON (GDELT answers errors as plain text with status 200).
    """
    try:
        body = json.loads(payload or b"{}")
    except json.JSONDecodeError:
        raise SourceError(f"GDELT answered text instead of JSON: {payload[:160]!r}") from None
    rows = []
    for series in body.get("timeline") or []:
        for p in series.get("data") or []:
            rows.append({"ts": _when(p["date"]), "value": float(p.get("value", 0.0)),
                         "norm": float(p["norm"]) if "norm" in p else float("nan")})
    return pd.DataFrame(rows, columns=["ts", "value", "norm"])


def daily(vol: pd.DataFrame, tone: pd.DataFrame) -> pd.DataFrame:
    """Daily volume, share of coverage and tone from timelines of any resolution.

    Hourly periods (GDELT's resolution for spans under about a week) are summed into days; tone is averaged with the
    hour's article count as weight, so a busy hour counts more than a quiet one.

    Args:
        vol: ``parse_timeline`` of ``timelinevolraw``.
        tone: ``parse_timeline`` of ``timelinetone``.

    Returns:
        Rows ``ts`` (UTC day), ``articles``, ``total`` (articles GDELT monitored), ``share`` (articles per 10,000
        monitored) and ``tone``.
    """
    v = vol.assign(day=vol["ts"].dt.normalize()).groupby("day")[["value", "norm"]].sum()
    t = tone.assign(day=tone["ts"].dt.normalize())
    if len(t):
        w = t.merge(vol[["ts", "value"]].rename(columns={"value": "w"}), on="ts", how="left")
        w["w"] = w["w"].fillna(1.0).clip(lower=1e-9)
        tone_d = (w["value"] * w["w"]).groupby(w["day"]).sum() / w["w"].groupby(w["day"]).sum()
    else:
        tone_d = pd.Series(dtype=float)
    out = pd.DataFrame({"articles": v["value"], "total": v["norm"]}).join(tone_d.rename("tone"), how="outer")
    out["share"] = 1e4 * out["articles"] / out["total"].where(out["total"] > 0)
    out.index.name = "ts"
    return out.reset_index()[["ts", "articles", "total", "share", "tone"]]


def _request(fetcher: store.Fetcher, query: str, mode: str, start: pd.Timestamp, end: pd.Timestamp,
             name: str, final: bool) -> bytes:
    """One DOC API request, cached.

    Args:
        fetcher: The radar's fetcher.
        query: The query, language filter included.
        mode: ``timelinevolraw`` or ``timelinetone``.
        start: First moment asked for.
        end: Moment after the last one asked for.
        name: Series name, for the cache file.
        final: Whether the span is over (cached forever) or still running (cached for an hour).

    Returns:
        The body.
    """
    params = {"query": query, "mode": mode, "format": "json", "startdatetime": f"{start:%Y%m%d%H%M%S}",
              "enddatetime": f"{end:%Y%m%d%H%M%S}"}
    cache = store.raw_path(SOURCE, f"{name}_{mode}_{start:%Y%m%d}_{end:%Y%m%d%H}.json")
    return fetcher.get(URL, params, cache=cache, ttl=None if final else 3600) or b"{}"


def headlines(fetcher: store.Fetcher, item: dict[str, Any], now: pd.Timestamp) -> list[dict[str, Any]]:
    """Recent matching articles as headline rows, scored with the radar's lexicon.

    Titles are untrusted text from anywhere on the web: they are stored and shown as data, never interpreted.

    Args:
        fetcher: The radar's fetcher.
        item: A ``gdelt`` watchlist entry.
        now: Current time.

    Returns:
        Rows for ``radar_headlines``.
    """
    params = {"query": item["query"] + LANGUAGE, "mode": "artlist", "format": "json", "maxrecords": HEADLINES,
              "sort": "datedesc", "timespan": "24h"}
    body = fetcher.get(URL, params, cache=store.raw_path(SOURCE, f"{item['id']}_artlist_{now:%Y%m%d%H}.json"),
                       ttl=3600) or b"{}"
    try:
        arts = json.loads(body).get("articles") or []
    except json.JSONDecodeError:
        return []
    out = []
    for a in arts:
        title = sentiment.clean(a.get("title", ""))
        if not title or not a.get("url"):
            continue
        score = sentiment.score(title)
        out.append({"source": SOURCE, "item_id": str(a["url"])[:500], "topic": item["id"], "title": title,
                    "url": str(a["url"])[:500], "domain": str(a.get("domain", ""))[:100],
                    "language": str(a.get("language", ""))[:30],
                    "published_at": _when(a["seendate"]) if a.get("seendate") else now, "fetched_at": now,
                    "points": None, **score})
    return out


def collect(items: list[dict[str, Any]], fetcher: store.Fetcher, now: datetime, deep: bool = False,
            log: Log | None = None) -> dict[str, Any]:
    """Update every watched query's daily volume and tone, and store recent headlines.

    Args:
        items: ``gdelt`` watchlist entries (``id``, ``query``, ``label``, ``category``).
        fetcher: The radar's fetcher.
        now: Current time (tz-aware).
        deep: Must be True: GDELT is only asked in the deep pass. False returns at once.
        log: Receives one line per query.

    Returns:
        The collector summary.
    """
    if not deep:
        return {"source": SOURCE, "ok": True, "series": 0, "rows": 0, "errors": [],
                "message": "skipped (GDELT is asked only in the deep daily pass)"}
    say = log or (lambda _m: None)
    t = pd.Timestamp(now).tz_convert("UTC")
    end = t.floor("h")
    n_series = n_rows = failures = 0
    errors: list[str] = []
    heads: list[dict[str, Any]] = []
    for item in items:
        if failures >= MAX_FAILURES:
            errors.append(f"{item['id']}: not asked (GDELT refused {failures} requests in a row)")
            continue
        name = store.slug(item["id"])
        query = item["query"] + LANGUAGE
        try:
            stored = store.series_path(SOURCE, name).exists()
            start = (FIRST if not stored else
                     max(FIRST, store.read(SOURCE, name)["ts"].max() - pd.Timedelta(days=REFETCH_DAYS)))
            vol = parse_timeline(_request(fetcher, query, "timelinevolraw", start, end, name, False))
            tone = parse_timeline(_request(fetcher, query, "timelinetone", start, end, name, False))
            failures = 0
            df = daily(vol, tone)
            if df.empty:
                errors.append(f"{item['id']}: GDELT returned no periods")
                continue
            df["partial"] = df["ts"] + pd.Timedelta(days=1) > t
            df["published_at"] = published(df["ts"] + pd.Timedelta(days=1), LAG, t)
            df.loc[df["partial"], "published_at"] = t
            df["fetched_at"] = t
            row = store.upsert(SOURCE, name, df, {"label": item.get("label", item["id"]),
                                                  "category": item.get("category", ""),
                                                  "unit": "articles per 10,000", "frequency": "1d"})
            n_series, n_rows = n_series + 1, n_rows + row["added"]
            say(f"gdelt {item['id']}: {row['n_rows']} days from {str(row['first_ts'])[:10]}, +{row['added']}")
            heads += headlines(fetcher, item, t)
        except SourceError as err:
            failures += 1
            errors.append(f"{item['id']}: {err}")
        except Exception as err:  # one query's failure must not stop the others
            errors.append(f"{item['id']}: {err}")
    stored_heads = store.add_headlines(heads)
    return summary(SOURCE, n_series, n_rows, errors, now, f"{stored_heads} headlines")

