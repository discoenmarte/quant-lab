"""Yahoo Finance chart API: daily bars for US ETFs, with adjusted close, dividends and splits.

This is an unofficial endpoint. It's used here for personal research only, one request per symbol with a
pause between symbols, and results are cached in the lab. If it stops working, keyed free alternatives are
Alpaca (IEX feed) and Tiingo.

Yahoo's ``close`` is split-adjusted but not dividend-adjusted; ``adjclose`` includes both. Both are kept.
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


def fetch(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full daily history and corporate events for one symbol.

    Args:
        symbol: Ticker, e.g. ``"SPY"``.

    Returns:
        ``(bars, events)`` as described in ``parse_chart``.

    Raises:
        SourceError: The request failed or returned no data.
    """
    now = int(datetime.now(UTC).timestamp())
    payload = get(URL.format(symbol=symbol, now=now))
    time.sleep(PAUSE_SECONDS)
    return parse_chart(payload)
