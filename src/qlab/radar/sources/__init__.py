"""The radar's collectors, one module per free source, all with the same shape.

Each module has ``collect(items, fetcher, now, deep, log)`` that fetches what is missing or still changing, stores
it with ``store.upsert`` and returns a summary ``{"source", "ok", "series", "rows", "errors", "message"}``. A failure
of one item (a market that closed, an article renamed) is reported and the others still run; a source that fails
entirely keeps its stored history, and the radar shows it as stale.

Quirks found while checking each endpoint on 2026-10-22 from the owner's network (Bogotá):

- **GDELT DOC 2.0** answers HTTP 429 to most requests, even 20 seconds apart, and took 11-16 s per answer. It is
  asked only in the deep daily pass, one request at a time, with long waits after a refusal.
- **Polymarket's price-history host** (``clob.polymarket.com``) resets the connection during the TLS handshake from
  this network, most likely an ISP block on betting sites. The radar doesn't work around blocks: it builds its own
  history from the market snapshots it takes (``gamma-api`` answers), and uses the history host where it is reachable.
- **Kalshi's** ``api.elections.kalshi.com`` failed about half the time; ``external-api.kalshi.com`` serves the same
  public market data and answered every time. Both are tried, in that order.
- **Metaculus** answers 403: its API now needs an account token. The lab doesn't create accounts for the owner, so it
  isn't collected.
- **FRED** limits the ICE BofA high-yield spread to its last three years (licensing); other FRED series are complete.
- **CBOE** redirects its history files from ``cdn.cboe.com`` to ``cdn-api.cboe.com``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pandas as pd

from ..store import mark_source

Log = Callable[[str], None]


def published(period_end: pd.Series | pd.Timestamp, lag: pd.Timedelta, fetched: pd.Timestamp) -> Any:
    """When values became public: the period's end plus the source's publication lag, but no later than the fetch.

    A value the radar holds was public at the latest when it was fetched, so a fetch soon after a period ends gives a
    tighter time than the conservative lag.

    Args:
        period_end: When each period ended (UTC).
        lag: The source's longest usual delay between a period's end and its publication.
        fetched: When the value was fetched.

    Returns:
        Same shape as ``period_end``.
    """
    t = period_end + lag
    if isinstance(t, pd.Series):
        return t.where(t <= fetched, fetched)
    return min(t, fetched)


def summary(source: str, series: int, rows: int, errors: list[str], now: datetime, note: str = "") -> dict[str, Any]:
    """A collector's summary, recorded as the source's status.

    Args:
        source: Source name.
        series: Series updated.
        rows: New rows stored.
        errors: One line per item that failed.
        now: Time of the collection.
        note: Extra words for the message (what was skipped and why).

    Returns:
        ``source, ok`` (some series updated or nothing failed), ``series, rows, errors, message``.
    """
    ok = series > 0 or not errors
    message = (f"{series} series updated, {rows} new rows" + (f"; {len(errors)} failed: {errors[0]}" if errors else "")
               + (f"; {note}" if note else ""))
    mark_source(source, ok, message, now)
    return {"source": source, "ok": ok, "series": series, "rows": rows, "errors": errors, "message": message}
