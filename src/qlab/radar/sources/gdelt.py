"""GDELT 2.0 DOC API: daily news volume and average tone for each watched query, and recent headlines.

GDELT monitors online news worldwide every 15 minutes. For a query, ``timelinevolraw`` gives the number of matching
articles per period with ``norm``, the number of articles GDELT monitored in that period, so the share of coverage is
comparable across years in which GDELT's own coverage grew; ``timelinetone`` gives the average tone of the matching
articles (GDELT's dictionary-based score, roughly -10 to +10); ``artlist`` gives recent matching articles.

- **Point in time.** A day's values settle within about an hour of the day's end (GDELT updates every 15 minutes), so
  rows are stamped as published one hour after their day ends. GDELT recomputes timelines from its current index:
  values fetched today for an old day are today's view of that day, not what a reader saw then. The radar keeps
  ``fetched_at`` so the difference can be measured where it matters.
- **Rate limits.** GDELT asks for at most one request every five seconds and, from the owner's network on 2026-10-22,
  accepted about one request in five even two minutes apart (decision 0016). So the work is spread over the day:
  each 15-minute pass makes at most two requests without retries, the deep pass at most twelve with long waits, and
  each query is refreshed once a day, the one tried longest ago first. A query's whole history (2017 on: GDELT refuses
  earlier start dates) comes in one request per mode.
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
REFRESH = pd.Timedelta(hours=20)  # a query's timelines are refreshed about once a day
QUICK_REQUESTS, DEEP_REQUESTS = 2, 12  # requests per pass
MAX_FAILURES = 2  # failed requests in a row before the deep pass leaves GDELT alone
HEADLINES = 50
HEADLINE_QUERIES = 3  # headlines are fetched for the first few queries only, once a day
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
             name: str, retries: int | None) -> bytes:
    """One DOC API request, cached.

    Args:
        fetcher: The radar's fetcher.
        query: The query, language filter included.
        mode: ``timelinevolraw`` or ``timelinetone``.
        start: First moment asked for.
        end: Moment after the last one asked for.
        name: Series name, for the cache file.
        retries: Retries after a refusal (None: the fetcher's backoff for GDELT).

    Returns:
        The body (cached for an hour, so a pass that got the volume but not the tone can reuse it).
    """
    params = {"query": query, "mode": mode, "format": "json", "startdatetime": f"{start:%Y%m%d%H%M%S}",
              "enddatetime": f"{end:%Y%m%d%H%M%S}"}
    cache = store.raw_path(SOURCE, f"{name}_{mode}_{start:%Y%m%d}_{end:%Y%m%d%H}.json")
    return fetcher.get(URL, params, cache=cache, ttl=3600, retries=retries) or b"{}"


def headlines(fetcher: store.Fetcher, item: dict[str, Any], now: pd.Timestamp,
              retries: int | None = None) -> list[dict[str, Any]]:
    """Recent matching articles as headline rows, scored with the radar's lexicon.

    Titles are untrusted text from anywhere on the web: they are stored and shown as data, never interpreted.

    Args:
        fetcher: The radar's fetcher.
        item: A ``gdelt`` watchlist entry.
        now: Current time.
        retries: Retries after a refusal (None: the fetcher's backoff).

    Returns:
        Rows for ``radar_headlines``.
    """
    params = {"query": item["query"] + LANGUAGE, "mode": "artlist", "format": "json", "maxrecords": HEADLINES,
              "sort": "datedesc", "timespan": "24h"}
    body = fetcher.get(URL, params, cache=store.raw_path(SOURCE, f"{item['id']}_artlist_{now:%Y%m%d%H}.json"),
                       ttl=3600, retries=retries) or b"{}"
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


def _state_path():
    """Where the collector remembers its attempts per query.

    Returns:
        ``<data>/raw/radar/gdelt/state.json``.
    """
    return store.raw_path(SOURCE, "state.json")


def _state() -> dict[str, dict[str, str]]:
    """The last attempt and success per query id (ISO times).

    Returns:
        ``{query id: {"attempt": ..., "success": ..., "headlines": ...}}``; empty when unreadable.
    """
    try:
        return json.loads(_state_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, dict[str, str]]) -> None:
    """Write the attempt log.

    Args:
        state: See ``_state``.
    """
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, sort_keys=True))


def _due(state: dict[str, str], key: str, t: pd.Timestamp) -> bool:
    """Whether a piece of work is due: never done, or done more than ``REFRESH`` ago.

    Args:
        state: One query's attempt log.
        key: ``success`` (the timelines) or ``headlines``.
        t: Current time.

    Returns:
        True when due.
    """
    done = state.get(key)
    return done is None or t - pd.Timestamp(done) > REFRESH


def collect(items: list[dict[str, Any]], fetcher: store.Fetcher, now: datetime, deep: bool = False,
            log: Log | None = None) -> dict[str, Any]:
    """Refresh the queries that are due, within a request budget; then headlines for the first few queries.

    A query is due when its timelines were last refreshed more than 20 hours ago. Due queries are worked through in the
    order of their oldest attempt, so a query GDELT keeps refusing doesn't starve the others. The quick pass makes at
    most two requests without retries; the deep pass at most twelve with GDELT's long backoff. A pass stops at the
    first refusal (quick) or after two in a row (deep).

    Args:
        items: ``gdelt`` watchlist entries (``id``, ``query``, ``label``, ``category``).
        fetcher: The radar's fetcher.
        now: Current time (tz-aware).
        deep: The deep pass (larger budget, retries).
        log: Receives one line per query.

    Returns:
        The collector summary; ``message`` says how many queries are still due.
    """
    say = log or (lambda _m: None)
    t = pd.Timestamp(now).tz_convert("UTC")
    end = t.floor("h")
    budget = DEEP_REQUESTS if deep else QUICK_REQUESTS
    retries = None if deep else 0
    give_up = MAX_FAILURES if deep else 1
    state = _state()
    n_series = n_rows = failures = used = 0
    errors: list[str] = []
    heads: list[dict[str, Any]] = []
    due = [it for it in items if _due(state.get(it["id"], {}), "success", t)]
    due.sort(key=lambda it: state.get(it["id"], {}).get("attempt", ""))
    for item in due:
        if used + 2 > budget or failures >= give_up:
            break
        name = store.slug(item["id"])
        query = item["query"] + LANGUAGE
        st = state.setdefault(item["id"], {})
        st["attempt"] = t.isoformat()
        try:
            stored = store.series_path(SOURCE, name).exists()
            start = (FIRST if not stored else
                     max(FIRST, store.read(SOURCE, name)["ts"].max() - pd.Timedelta(days=REFETCH_DAYS)))
            used += 1
            vol = parse_timeline(_request(fetcher, query, "timelinevolraw", start, end, name, retries))
            used += 1
            tone = parse_timeline(_request(fetcher, query, "timelinetone", start, end, name, retries))
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
            st["success"] = t.isoformat()
            n_series, n_rows = n_series + 1, n_rows + row["added"]
            say(f"gdelt {item['id']}: {row['n_rows']} days from {str(row['first_ts'])[:10]}, +{row['added']}")
        except SourceError as err:
            failures += 1
            errors.append(f"{item['id']}: {err}")
        except Exception as err:  # one query's failure must not stop the others
            errors.append(f"{item['id']}: {err}")
    for item in items[:HEADLINE_QUERIES]:
        st = state.setdefault(item["id"], {})
        if used >= budget or failures >= give_up or not _due(st, "headlines", t):
            continue
        try:
            used += 1
            heads += headlines(fetcher, item, t, retries)
            st["headlines"] = t.isoformat()
            failures = 0
        except SourceError as err:
            failures += 1
            errors.append(f"{item['id']} headlines: {err}")
    _save_state(state)
    stored_heads = store.add_headlines(heads)
    left = sum(_due(state.get(it["id"], {}), "success", t) for it in items)
    note = f"{stored_heads} headlines; {left} of {len(items)} queries still due ({used} requests this pass)"
    if not n_series and not errors:
        return {"source": SOURCE, "ok": True, "series": 0, "rows": 0, "errors": [], "message": note}
    return summary(SOURCE, n_series, n_rows, errors, now, note)
