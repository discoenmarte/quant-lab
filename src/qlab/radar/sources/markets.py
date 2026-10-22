"""Prediction markets as crowd forecasts: Polymarket and Kalshi prices for the curated scenarios.

A market's price for "Yes" is the crowd's probability, net of a small premium and of whatever moves thin markets.
The radar reads it, never trades it.

- **The probability** is the middle of the best bid and ask when the spread is at most 10 cents, otherwise the last
  trade, otherwise the quoted price. Each row keeps the bid, the ask and the last trade too.
- **Snapshots** are taken every pass and stored in 15-minute buckets (a later snapshot in the same bucket replaces
  the earlier one). ``published_at`` is the fetch time: a quote is public the moment it is shown.
- **History.** Polymarket's history host is unreachable from the owner's network (see ``sources``); where it answers,
  the deep pass stores its daily prices. Kalshi's daily candles come with the deep pass from the public API.
- A series is named ``<scenario>__<market>``, so the scenario board can group markets by question.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from ...data.http import SourceError
from .. import store
from . import Log, summary

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com/prices-history"
KALSHI = ("https://external-api.kalshi.com/trade-api/v2", "https://api.elections.kalshi.com/trade-api/v2")
BUCKET = "15min"
MAX_SPREAD = 0.10
KALSHI_HISTORY_DAYS = 400


def _num(x: Any) -> float | None:
    """A price as a float.

    Args:
        x: Number, numeric string or None.

    Returns:
        The value, or None when missing or not a number.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def probability(bid: float | None, ask: float | None, last: float | None, quoted: float | None) -> float | None:
    """The crowd's probability from a market's quotes.

    Args:
        bid: Best bid for Yes (0-1).
        ask: Best ask for Yes (0-1).
        last: Last trade price.
        quoted: The venue's displayed price.

    Returns:
        The mid when both sides exist and the spread is at most 10 cents, else the last trade, else the quoted price;
        None when there is nothing.
    """
    if bid is not None and ask is not None and 0 < ask - bid <= MAX_SPREAD:
        return (bid + ask) / 2
    if bid is not None and ask is not None and ask == bid:
        return bid
    return last if last is not None else quoted


def series_name(scenario: dict[str, Any], market: str) -> str:
    """The series name of one market of a scenario.

    Args:
        scenario: A ``scenarios`` watchlist entry.
        market: The market's slug or ticker.

    Returns:
        ``<scenario id>__<market>`` as a slug.
    """
    return store.slug(f"{scenario['id']}__{market}", limit=120)


def _snapshot(rows: list[dict[str, Any]], scenario: dict[str, Any], market: str, label: str,
              t: pd.Timestamp) -> dict[str, Any]:
    """Store one market's rows.

    Args:
        rows: Dicts with ``ts`` and the price columns; ``published_at`` defaults to the fetch time.
        scenario: The scenario.
        market: Market slug or ticker.
        label: What the market asks, for the catalog.
        t: Fetch time.

    Returns:
        The catalog row from ``store.upsert``.
    """
    df = pd.DataFrame(rows)
    if "published_at" not in df:
        df["published_at"] = t
    df["fetched_at"] = t
    df["partial"] = False
    return store.upsert("markets", series_name(scenario, market), df,
                        {"label": label, "category": "scenario", "unit": "probability", "frequency": BUCKET})


def polymarket(scenario: dict[str, Any], fetcher: store.Fetcher, t: pd.Timestamp, deep: bool,
               history: dict[str, bool]) -> list[dict[str, Any]]:
    """Snapshot (and in the deep pass, history) of a Polymarket event's open markets.

    Args:
        scenario: Entry with ``event`` (the event slug) and optional ``max_markets`` (default 3).
        fetcher: The radar's fetcher.
        t: Current time.
        deep: Also try the history host.
        history: Shared flag ``{"reachable": bool}``; set False after the history host fails once.

    Returns:
        Catalog rows of the markets stored.

    Raises:
        SourceError: The event is unknown or the API failed.
    """
    body = fetcher.get(f"{GAMMA}/events", {"slug": scenario["event"]},
                       cache=store.raw_path("polymarket", f"{store.slug(scenario['event'])}.json"), ttl=600)
    events = json.loads(body or b"[]")
    if not events:
        raise SourceError(f"no Polymarket event {scenario['event']}")
    markets = [m for m in events[0].get("markets") or [] if not m.get("closed")]
    markets.sort(key=lambda m: -(_num(m.get("volumeNum") or m.get("volume")) or 0.0))
    out = []
    bucket = t.floor(BUCKET)
    for m in markets[: int(scenario.get("max_markets", 3))]:
        prices = json.loads(m.get("outcomePrices") or "[]")
        bid, ask, last = _num(m.get("bestBid")), _num(m.get("bestAsk")), _num(m.get("lastTradePrice"))
        p = probability(bid, ask, last, _num(prices[0]) if prices else None)
        if p is None:
            continue
        label = scenario["title"] + (f": {m['groupItemTitle']}" if m.get("groupItemTitle") else "")
        rows = [{"ts": bucket, "prob": p, "bid": bid, "ask": ask, "last": last,
                 "volume24h": _num(m.get("volume24hr"))}]
        if deep and history.get("reachable", True):
            tokens = json.loads(m.get("clobTokenIds") or "[]")
            try:
                hist = fetcher.get(CLOB, {"market": tokens[0], "interval": "max", "fidelity": 1440},
                                   cache=store.raw_path("polymarket", f"history_{store.slug(m['slug'])}.json"),
                                   ttl=20 * 3600) if tokens else None
                for pt in json.loads(hist or b"{}").get("history") or []:
                    when = pd.Timestamp(int(pt["t"]), unit="s", tz="UTC")
                    rows.append({"ts": when.floor(BUCKET), "prob": float(pt["p"]), "published_at": when})
            except SourceError:
                history["reachable"] = False
        out.append(_snapshot(rows, scenario, m["slug"], label, t))
    return out


def _kalshi(fetcher: store.Fetcher, path: str, params: dict[str, Any] | None, cache: str, ttl: float) -> dict:
    """GET from Kalshi's public API, trying each host in turn.

    Args:
        fetcher: The radar's fetcher.
        path: Path below ``/trade-api/v2``.
        params: Query parameters.
        cache: Cache file name.
        ttl: Cache lifetime in seconds.

    Returns:
        The decoded body.

    Raises:
        SourceError: Every host failed.
    """
    last: Exception | None = None
    for base in KALSHI:
        try:
            return json.loads(fetcher.get(base + path, params, cache=store.raw_path("kalshi", cache), ttl=ttl)
                              or b"{}")
        except SourceError as err:
            last = err
    raise SourceError(f"kalshi: every host failed ({last})")


def kalshi(scenario: dict[str, Any], fetcher: store.Fetcher, t: pd.Timestamp, deep: bool) -> list[dict[str, Any]]:
    """Snapshot (and in the deep pass, daily candles) of one Kalshi market.

    Args:
        scenario: Entry with ``ticker`` and ``series``.
        fetcher: The radar's fetcher.
        t: Current time.
        deep: Also store daily candles for the last ``KALSHI_HISTORY_DAYS`` days.

    Returns:
        The catalog row, in a list.

    Raises:
        SourceError: The market is unknown or the API failed.
    """
    tk = scenario["ticker"]
    m = _kalshi(fetcher, f"/markets/{tk}", None, f"{store.slug(tk)}.json", 600).get("market") or {}
    if not m:
        raise SourceError(f"no Kalshi market {tk}")
    bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
    last = _num(m.get("last_price_dollars"))
    p = probability(bid, ask, last, last)
    rows = [] if p is None else [{"ts": t.floor(BUCKET), "prob": p, "bid": bid, "ask": ask, "last": last,
                                  "volume24h": _num(m.get("volume_24h_fp"))}]
    if deep:
        end = int(t.timestamp())
        body = _kalshi(fetcher, f"/series/{scenario['series']}/markets/{tk}/candlesticks",
                       {"start_ts": end - KALSHI_HISTORY_DAYS * 86400, "end_ts": end, "period_interval": 1440},
                       f"candles_{store.slug(tk)}_{t:%Y%m%d}.json", 20 * 3600)
        for c in body.get("candlesticks") or []:
            price, yb, ya = c.get("price") or {}, c.get("yes_bid") or {}, c.get("yes_ask") or {}
            cb, ca = _num(yb.get("close_dollars")), _num(ya.get("close_dollars"))
            pc = probability(cb, ca, _num(price.get("close_dollars")), None)
            if pc is None:
                continue
            when = pd.Timestamp(int(c["end_period_ts"]), unit="s", tz="UTC")
            rows.append({"ts": when.floor(BUCKET), "prob": pc, "bid": cb, "ask": ca,
                         "last": _num(price.get("close_dollars")), "published_at": when})
    if not rows:
        raise SourceError(f"Kalshi market {tk} has no price")
    label = scenario["title"]
    return [_snapshot(rows, scenario, tk, label, t)]


def collect(items: list[dict[str, Any]], fetcher: store.Fetcher, now: datetime, deep: bool = False,
            log: Log | None = None) -> dict[str, Any]:
    """Snapshot every curated scenario's markets.

    Args:
        items: ``scenarios`` watchlist entries.
        fetcher: The radar's fetcher.
        now: Current time (tz-aware).
        deep: Also fetch history.
        log: Receives one line per scenario.

    Returns:
        The collector summary (source ``markets``).
    """
    say = log or (lambda _m: None)
    t = pd.Timestamp(now).tz_convert("UTC")
    n_series = n_rows = 0
    errors: list[str] = []
    history = {"reachable": True}
    for sc in items:
        try:
            if sc.get("source") == "kalshi":
                rows = kalshi(sc, fetcher, t, deep)
            elif sc.get("source") == "polymarket":
                rows = polymarket(sc, fetcher, t, deep, history)
            else:
                raise SourceError(f"unknown market source {sc.get('source')!r}")
            n_series, n_rows = n_series + len(rows), n_rows + sum(r["added"] for r in rows)
            say(f"markets {sc['id']}: {len(rows)} markets")
        except Exception as err:  # one scenario's failure must not stop the others
            errors.append(f"{sc['id']}: {err}")
    note = "" if history["reachable"] or not deep else "Polymarket's history host unreachable; snapshots only"
    return summary("markets", n_series, n_rows, errors, now, note)
