"""Market stress from free official files: CBOE's VIX and VIX3M, FRED series, and the crypto Fear & Greed index.

- **CBOE** publishes each index's full daily history as one CSV (dates as MM/DD/YYYY). A close is public about 15
  minutes after the 16:00 New York close; rows are stamped as published at the next UTC midnight.
- **FRED** serves any series as CSV from ``fredgraph.csv`` with no key. The request timed out from this network in
  the past, so failures are reported and the stored history kept. The ICE BofA high-yield spread is limited to its
  last three years on FRED (licensing), so its history starts three years back. Rows are stamped as published two
  days after the observation (FRED posts these series the next business day).
- **Fear & Greed** (alternative.me) is one daily reading since 2018-02 for crypto, built from volatility, momentum,
  social media and surveys; it is stamped at 00:00 UTC of its day and published then.
- Every deep pass refetches the whole file: they are small, and sources revise recent values.
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any

import pandas as pd

from .. import store
from . import Log, published, summary

CBOE = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{symbol}_History.csv"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FNG = "https://api.alternative.me/fng/"
CBOE_LAG = pd.Timedelta(0)  # stamped at the next midnight UTC, after the 20:15-21:15 UTC publication
FRED_LAG = pd.Timedelta(days=1)
FNG_LAG = pd.Timedelta(hours=1)


def parse_cboe(payload: bytes, fetched: pd.Timestamp) -> pd.DataFrame:
    """Daily index values from a CBOE history file.

    Args:
        payload: The CSV.
        fetched: Fetch time.

    Returns:
        Rows ``ts, open, high, low, close`` plus the base columns.
    """
    df = pd.read_csv(io.BytesIO(payload))
    df.columns = [c.strip().lower() for c in df.columns]
    df["ts"] = pd.to_datetime(df["date"], format="%m/%d/%Y", utc=True)
    df = df[["ts", "open", "high", "low", "close"]].dropna(subset=["close"])
    df["published_at"] = published(df["ts"] + pd.Timedelta(days=1), CBOE_LAG, fetched)
    df["fetched_at"] = fetched
    return df


def parse_fred(payload: bytes, fetched: pd.Timestamp) -> pd.DataFrame:
    """Daily values from a FRED graph CSV (missing values are ``.`` or empty and are dropped).

    Args:
        payload: The CSV.
        fetched: Fetch time.

    Returns:
        Rows ``ts, value`` plus the base columns.
    """
    df = pd.read_csv(io.BytesIO(payload))
    date_col, value_col = df.columns[0], df.columns[1]
    out = pd.DataFrame({"ts": pd.to_datetime(df[date_col], utc=True),
                        "value": pd.to_numeric(df[value_col], errors="coerce")}).dropna()
    out["published_at"] = published(out["ts"] + pd.Timedelta(days=1), FRED_LAG, fetched)
    out["fetched_at"] = fetched
    return out


def parse_fng(payload: bytes, fetched: pd.Timestamp) -> pd.DataFrame:
    """Daily Fear & Greed readings.

    Args:
        payload: The JSON answer.
        fetched: Fetch time.

    Returns:
        Rows ``ts, value`` (0 extreme fear to 100 extreme greed) plus the base columns.
    """
    data = json.loads(payload).get("data") or []
    out = pd.DataFrame({"ts": pd.to_datetime([int(d["timestamp"]) for d in data], unit="s", utc=True),
                        "value": [float(d["value"]) for d in data]})
    out["published_at"] = published(out["ts"], FNG_LAG, fetched)
    out["fetched_at"] = fetched
    return out


def collect(items: dict[str, Any], fetcher: store.Fetcher, now: datetime, deep: bool = False,
            log: Log | None = None) -> dict[str, Any]:
    """Update every stress series.

    Args:
        items: The ``stress`` watchlist section (``cboe``, ``fred``, ``fng`` lists).
        fetcher: The radar's fetcher.
        now: Current time (tz-aware).
        deep: Fetch even when the cached file is recent (the quick pass reuses files younger than six hours).
        log: Receives one line per series.

    Returns:
        The collector summary (source ``stress``).
    """
    say = log or (lambda _m: None)
    t = pd.Timestamp(now).tz_convert("UTC")
    ttl = 3600 if deep else 6 * 3600
    n_series = n_rows = 0
    errors: list[str] = []
    jobs = ([("cboe", x["symbol"], x, lambda x: (CBOE.format(symbol=x["symbol"]), None), parse_cboe, "index")
             for x in items.get("cboe", [])]
            + [("fred", x["series"], x, lambda x: (FRED, {"id": x["series"]}), parse_fred, x.get("unit", ""))
               for x in items.get("fred", [])]
            + [("fng", x["id"], x, lambda x: (FNG, {"limit": 0, "format": "json"}), parse_fng, "0-100")
               for x in items.get("fng", [])])
    for src, key, item, where, parse, unit in jobs:
        name = store.slug(key)
        try:
            url, params = where(item)
            body = fetcher.get(url, params, cache=store.raw_path(src, f"{name}.{'json' if src == 'fng' else 'csv'}"),
                               ttl=ttl)
            df = parse(body, t)
            if df.empty:
                errors.append(f"{src} {key}: empty answer")
                continue
            row = store.upsert(src, name, df, {"label": item.get("label", key), "category": "stress", "unit": unit,
                                               "frequency": "1d"})
            n_series, n_rows = n_series + 1, n_rows + row["added"]
            say(f"{src} {key}: {row['n_rows']} days through {str(row['last_ts'])[:10]}, +{row['added']}")
        except Exception as err:  # one file's failure must not stop the others
            errors.append(f"{src} {key}: {err}")
    return summary("stress", n_series, n_rows, errors, now)
