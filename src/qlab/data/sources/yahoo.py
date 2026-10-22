"""Yahoo Finance chart API: daily bars for US ETFs, with adjusted close, dividends and splits.

This is an unofficial endpoint. It's used here for personal research only, one request per symbol with a
pause between symbols, and results are cached in the lab. If it stops working, keyed free alternatives are
Alpaca (IEX feed) and Tiingo.

Yahoo's ``close`` is split-adjusted but not dividend-adjusted; ``adjclose`` includes both. Both are kept.

The full-history query can lag: on 2026-10-19 and 2026-10-21 it still ended on the previous session at 20:30 New
York, four hours after the close, so the paper book had no bar to decide on. A second, five-day query is served
fresher, so ``fetch`` appends any later sessions it has, rescaled onto the full history's dividend basis.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pandas as pd

from ..bars import normalize_bars
from ..http import SourceError, get

URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1=0&period2={now}&interval=1d&events=div%2Csplit&includeAdjustedClose=true"
)
RECENT_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?range=5d&interval=1d&events=div%2Csplit&includeAdjustedClose=true"
)
PAUSE_SECONDS = 0.5


def parse_chart(payload: bytes | str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse a chart API response.

    Args:
        payload: JSON body of ``/v8/finance/chart/<symbol>``.

    Returns:
        ``(bars, events)``. Bars have ``ts`` (session date at 00:00 UTC), ``open, high, low, close,
        adj_close, volume``. Events have ``ts, kind`` (``"dividend"`` or ``"split"``) and ``value`` (cash
        amount, or the split ratio as a float).

    Raises:
        SourceError: The response has no chart result.
    """
    data = json.loads(payload)
    result = (data.get("chart") or {}).get("result")
    if not result:
        raise SourceError(f"Yahoo returned no chart result: {(data.get('chart') or {}).get('error')}")
    res = result[0]
    tz = res["meta"].get("exchangeTimezoneName", "America/New_York")
    stamps = pd.to_datetime(res.get("timestamp", []), unit="s", utc=True)
    # Yahoo stamps daily bars at the session open; the bar belongs to that session's date.
    session = stamps.tz_convert(tz).normalize().tz_localize(None).tz_localize("UTC")
    quote = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    bars = pd.DataFrame(
        {
            "ts": session,
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "adj_close": adj if adj is not None else quote.get("close"),
            "volume": quote.get("volume"),
        }
    ).dropna(subset=["open", "high", "low", "close"])

    events = []
    for kind, items in (res.get("events") or {}).items():
        for item in items.values():
            when = pd.Timestamp(item["date"], unit="s", tz="UTC").tz_convert(tz).normalize()
            ts = when.tz_localize(None).tz_localize("UTC")
            if kind == "dividends":
                events.append({"ts": ts, "kind": "dividend", "value": float(item["amount"])})
            elif kind == "splits":
                ratio = float(item["numerator"]) / float(item["denominator"])
                events.append({"ts": ts, "kind": "split", "value": ratio})
    events_df = pd.DataFrame(events, columns=["ts", "kind", "value"]).sort_values("ts").reset_index(drop=True)
    return normalize_bars(bars), events_df


def merge_recent(bars: pd.DataFrame, events: pd.DataFrame, recent: pd.DataFrame,
                 recent_events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Append the sessions a short query has and the full history lacks.

    Both queries adjust for dividends relative to the newest session each one knows, so if a dividend went ex on a
    session only the short query has, its adjusted closes are a constant factor below the full history's on the
    sessions they share. The appended bars are divided by that factor (taken on the last shared session), which keeps
    every adjusted return right.

    Args:
        bars: Full history, as returned by ``parse_chart``.
        events: Its events.
        recent: Bars from the short query.
        recent_events: Its events.

    Returns:
        ``(bars, events)`` with the later sessions and their events appended; unchanged when the short query has
        nothing newer or no session in common with the full history.
    """
    if bars.empty or recent.empty:
        return bars, events
    last = bars["ts"].max()
    new = recent[recent["ts"] > last]
    shared = recent[recent["ts"] == last]
    if new.empty or shared.empty:
        return bars, events
    old = bars.loc[bars["ts"] == last, "adj_close"].iloc[-1]
    factor = float(shared["adj_close"].iloc[-1]) / float(old) if old else 1.0
    new = new.assign(adj_close=new["adj_close"] / factor)
    merged = normalize_bars(pd.concat([bars, new], ignore_index=True))
    extra = recent_events[recent_events["ts"] > last] if not recent_events.empty else recent_events
    if not extra.empty:
        events = (pd.concat([events, extra], ignore_index=True).drop_duplicates(["ts", "kind"])
                  .sort_values("ts").reset_index(drop=True))
    return merged, events


def fetch(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full daily history and corporate events for one symbol, topped up with the latest sessions.

    Args:
        symbol: Ticker, e.g. ``"SPY"``.

    Returns:
        ``(bars, events)`` as described in ``parse_chart``. A failed short query leaves the full history as it is.

    Raises:
        SourceError: The full-history request failed or returned no data.
    """
    now = int(datetime.now(UTC).timestamp())
    payload = get(URL.format(symbol=symbol, now=now))
    time.sleep(PAUSE_SECONDS)
    bars, events = parse_chart(payload)
    try:
        recent, recent_events = parse_chart(get(RECENT_URL.format(symbol=symbol)))
    except (SourceError, KeyError, ValueError, TypeError):  # the short query only tops up; never fail on it
        return bars, events
    finally:
        time.sleep(PAUSE_SECONDS)
    return merge_recent(bars, events, recent, recent_events)
