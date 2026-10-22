"""The scenario board: what prediction markets say about the curated questions, how that changed, and base rates.

Prediction-market prices aggregate many people's money-weighted forecasts and have a good record against polls and
experts on questions that resolve (Wolfers & Zitzewitz, 2004; Arrow et al., 2008). They are also thin on long-dated
questions, carry a premium for holding capital to the end, and can be moved by a few large traders. The board shows
them as information. A probability here is never a trading signal unless a pre-registered gauntlet test says so.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from ..timeutil import now_utc
from . import store, watchlist
from .sources import markets

NOTE = ("Crowd forecasts from prediction markets, read as information. A probability here is never a trading signal "
        "unless a pre-registered gauntlet test says so.")
HISTORY_DAYS = 180


def _link(sc: dict[str, Any]) -> str:
    """The market's public page.

    Args:
        sc: A scenario.

    Returns:
        URL on polymarket.com or kalshi.com.
    """
    if sc.get("source") == "kalshi":
        return f"https://kalshi.com/markets/{str(sc.get('series', '')).lower()}"
    return f"https://polymarket.com/event/{sc.get('event', '')}"


def _at(s: pd.Series, when: pd.Timestamp) -> float | None:
    """The last value at or before a time.

    Args:
        s: Probabilities by time.
        when: Time.

    Returns:
        The value, or None when the series starts later.
    """
    before = s[s.index <= when]
    return float(before.iloc[-1]) if len(before) else None


def market_view(name: str, now: pd.Timestamp) -> dict[str, Any]:
    """One stored market as the board shows it.

    Args:
        name: Series name (``<scenario>__<market>``).
        now: Current time.

    Returns:
        ``name, label, prob, as_of, change_1d, change_7d, change_30d`` (in probability points; None without
        history that far back), ``bid, ask`` and ``history`` (daily last values for the last 180 days:
        ``[{"ts", "prob"}]``).
    """
    df = store.read("markets", name)
    s = pd.Series(df["prob"].to_numpy(float), index=df["ts"]).sort_index()
    last = df.sort_values("ts").iloc[-1]
    prob = float(s.iloc[-1])

    def change(days: int) -> float | None:
        before = _at(s, s.index[-1] - pd.Timedelta(days=days))
        return None if before is None else prob - before

    daily = s.groupby(s.index.floor("D")).last()
    daily = daily[daily.index >= now - pd.Timedelta(days=HISTORY_DAYS)]
    cat = next((r for r in store.catalog("markets") if r["name"] == name), {})
    return {"name": name, "label": cat.get("label", name), "prob": prob, "as_of": s.index[-1].isoformat(),
            "change_1d": change(1), "change_7d": change(7), "change_30d": change(30),
            "bid": None if pd.isna(last.get("bid")) else float(last["bid"]),
            "ask": None if pd.isna(last.get("ask")) else float(last["ask"]),
            "history": [{"ts": t.isoformat(), "prob": float(v)} for t, v in daily.items()]}


def board(now: datetime | None = None) -> dict[str, Any]:
    """The scenario board.

    Args:
        now: Current time (None means now).

    Returns:
        ``note`` and ``scenarios``: per curated question ``id, title, category, source, link, base_rate`` and
        ``markets`` (see ``market_view``; empty until the first collection).
    """
    t = pd.Timestamp(now or now_utc()).tz_convert("UTC")
    stored = [r["name"] for r in store.catalog("markets")]
    out = []
    for sc in watchlist.load()["scenarios"]:
        prefix = store.slug(sc["id"]) + "__"
        names = [n for n in stored if n.startswith(prefix)]
        views = [market_view(n, t) for n in names]
        views.sort(key=lambda v: -v["prob"])
        out.append({"id": sc["id"], "title": sc["title"], "category": sc.get("category", ""),
                    "source": sc.get("source", ""), "link": _link(sc), "base_rate": sc.get("base_rate"),
                    "markets": views})
    return {"note": NOTE, "scenarios": out}


def search(term: str, fetcher: store.Fetcher | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Open Polymarket events matching a search, to help the owner add scenarios to the watchlist.

    Args:
        term: Words to search for.
        fetcher: The radar's fetcher (None makes one).
        limit: Events to return.

    Returns:
        ``slug, title, end, volume`` and ``markets`` (``slug``, ``label``, ``prob``), most traded first.
    """
    f = fetcher or store.Fetcher()
    body = f.get(f"{markets.GAMMA}/public-search", {"q": term, "events_status": "active"})
    events = [e for e in json.loads(body or b"{}").get("events") or [] if not e.get("closed")]
    events.sort(key=lambda e: -(markets.num(e.get("volume")) or 0.0))
    out = []
    for e in events[:limit]:
        ms = []
        for m in e.get("markets") or []:
            if m.get("closed"):
                continue
            prices = json.loads(m.get("outcomePrices") or "[]")
            ms.append({"slug": m.get("slug"), "label": m.get("groupItemTitle") or m.get("question"),
                       "prob": markets.num(prices[0]) if prices else None})
        out.append({"slug": e.get("slug"), "title": e.get("title"), "end": (e.get("endDate") or "")[:10],
                    "volume": markets.num(e.get("volume")), "markets": ms})
    return out
