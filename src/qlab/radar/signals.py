"""The radar's signals: each stored series turned into the daily values its detectors watch.

A signal pairs what is shown (``level``: views, a spread, a probability) with what is tested (``x``: usually a log or a
change, so that a day's distance from normal means the same across calm and busy years), a watched direction, a
baseline window, and the detectors that suit it:

| Signal | x (what the detectors see) | Direction | Window |
|---|---|---|---|
| Wikipedia views, HN stories | log(1 + count) | up | 90 days |
| GDELT volume | log(share of coverage) | up | 90 days |
| GDELT tone | average tone | down | 90 days |
| Prediction market | daily change in probability | both | 60 days |
| VIX | log(VIX) | up | 250 days |
| VIX term structure | VIX / VIX3M (above 1 is an inversion) | up | 250 days |
| High-yield spread | 5-day change | up | 250 days |
| 10y-2y Treasury spread | 5-day change | both | 250 days |
| Crypto Fear & Greed | the index | down | 90 days |
| Index and crypto returns | daily log return | both | 250 days |
| Realised volatility | log of 5-day realised volatility | up | 250 days |
| Cross-asset correlation | 20-day average pairwise correlation | up | 250 days |

Every value is indexed by the UTC day it describes; a series whose newest day is a running day carries it (its
value is partial and the detectors compare it with full days, which makes upward spikes conservative).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..data import store as lake
from ..data.bars import adjusted
from . import store, watchlist
from .sources import wiki

ALL = ("robust_z", "cusum", "bocpd")
MIN_HISTORY = 60  # days of a signal's own history before detectors run on it
CORR_SET = ("SPY", "QQQ", "TLT", "GLD", "HYG")
CORR_WINDOW = 20
RV_WINDOW = 5
CHANGE_DAYS = 5
MARKET_JUMP = {"notable": 0.10, "severe": 0.20}  # a stated prior for markets with too little history (see scan)


@dataclass
class Signal:
    """One watched daily signal.

    Attributes:
        key: Unique id, ``<source>/<name>`` plus ``:<variant>`` when one series gives several signals.
        label: Plain-words name.
        category: A key of ``watchlist.CATEGORIES``.
        direction: ``up``, ``down`` or ``both``.
        window: Baseline length in observations.
        describe: What ``x`` is, in words.
        unit: Unit of ``level``.
        x: What the detectors test, by UTC day.
        level: What is shown, by UTC day.
        detectors: Detectors that run on it.
        published: When the newest value became public (None for the lab's own prices).
    """

    key: str
    label: str
    category: str
    direction: str
    window: int
    describe: str
    unit: str
    x: pd.Series
    level: pd.Series
    detectors: tuple[str, ...] = ALL
    published: pd.Timestamp | None = None


def _daily(df: pd.DataFrame, col: str) -> pd.Series:
    """One column of a stored series by UTC day (the last value of each day).

    Args:
        df: A radar series.
        col: Column.

    Returns:
        Float series indexed by tz-aware UTC midnight.
    """
    s = df.set_index(df["ts"].dt.floor("D"))[col].astype(float)
    return s.groupby(level=0).last().sort_index()


def _try(source: str, name: str) -> pd.DataFrame | None:
    """A stored radar series, or None when it hasn't been collected.

    Args:
        source: Source name.
        name: Series name.

    Returns:
        The frame or None.
    """
    try:
        return store.read(source, name)
    except FileNotFoundError:
        return None


def attention(wl: dict[str, Any]) -> list[Signal]:
    """Wikipedia views, Hacker News stories, GDELT volume and GDELT tone.

    Args:
        wl: The watchlist.

    Returns:
        Signals for every collected series.
    """
    out = []
    for it in wl["wiki"]:
        df = _try("wiki", wiki.name_of(it))
        if df is not None and len(df):
            v = _daily(df, "views")
            out.append(Signal(f"wiki/{wiki.name_of(it)}", it.get("label", it["article"]), it.get("category", "macro"),
                              "up", 90, "log of daily views", "views/day", np.log1p(v), v,
                              published=df["published_at"].max()))
    for it in wl["hn"]:
        name = store.slug(it["keyword"])
        df = _try("hn", name)
        if df is not None and len(df):
            v = _daily(df, "stories")
            out.append(Signal(f"hn/{name}", it.get("label", it["keyword"]), it.get("category", "ai"), "up", 90,
                              "log of 1 + stories per day", "stories/day", np.log1p(v), v,
                              published=df["published_at"].max()))
    for it in wl["gdelt"]:
        name = store.slug(it["id"])
        df = _try("gdelt", name)
        if df is None or not len(df):
            continue
        share, tone = _daily(df, "share"), _daily(df, "tone")
        label = it.get("label", it["id"])
        out.append(Signal(f"gdelt/{name}:volume", label + " (volume)", it.get("category", "macro"), "up", 90,
                          "log of matching articles per 10,000 monitored", "per 10,000",
                          np.log(share.where(share > 0)), share, published=df["published_at"].max()))
        out.append(Signal(f"gdelt/{name}:tone", label + " (tone)", it.get("category", "macro"), "down", 90,
                          "average tone of matching articles", "tone", tone, tone, published=df["published_at"].max()))
    return out


def scenario_signals(wl: dict[str, Any]) -> list[Signal]:
    """Daily changes in each followed prediction market's probability.

    Args:
        wl: The watchlist.

    Returns:
        One signal per stored market of a curated scenario (robust z-scores only: CUSUM and change points need more
        history than most markets have).
    """
    out = []
    for row in store.catalog("markets"):
        sc_id = row["name"].split("__", 1)[0]
        sc = next((s for s in wl["scenarios"] if store.slug(s["id"]) == sc_id), None)
        if sc is None:
            continue
        df = store.read("markets", row["name"])
        p = _daily(df, "prob")
        out.append(Signal(f"markets/{row['name']}", row["label"], "scenario", "both", 60,
                          "daily change in the probability (last quote of each day)", "probability", p.diff(), p,
                          detectors=("robust_z",), published=df["published_at"].max()))
    return out


def stress_signals() -> list[Signal]:
    """VIX, its term structure, credit and curve spreads, and crypto Fear & Greed.

    Returns:
        Signals for every collected stress series.
    """
    out = []
    vix, vix3m = _try("cboe", "vix"), _try("cboe", "vix3m")
    if vix is not None and len(vix):
        v = _daily(vix, "close")
        out.append(Signal("cboe/vix", "VIX (30-day implied volatility of the S&P 500)", "stress", "up", 250,
                          "log of the VIX close", "index", np.log(v), v, published=vix["published_at"].max()))
        if vix3m is not None and len(vix3m):
            ratio = (v / _daily(vix3m, "close")).dropna()
            out.append(Signal("cboe/vix:term", "VIX / VIX3M (above 1: the term structure is inverted)", "stress",
                              "up", 250, "ratio of 30-day to 3-month implied volatility", "ratio", ratio, ratio,
                              detectors=("robust_z", "cusum"), published=vix3m["published_at"].max()))
    for sid, label, direction in (("bamlh0a0hym2", "US high-yield spread", "up"),
                                  ("t10y2y", "10-year minus 2-year Treasury yield", "both")):
        df = _try("fred", sid)
        if df is not None and len(df):
            lv = _daily(df, "value")
            out.append(Signal(f"fred/{sid}", label, "stress", direction, 250,
                              f"{CHANGE_DAYS}-observation change in percentage points", "%", lv.diff(CHANGE_DAYS), lv,
                              published=df["published_at"].max()))
    fng = _try("fng", "crypto")
    if fng is not None and len(fng):
        lv = _daily(fng, "value")
        out.append(Signal("fng/crypto", "Crypto Fear & Greed (0 extreme fear, 100 extreme greed)", "crypto", "down",
                          90, "the index", "0-100", lv, lv, detectors=("robust_z", "cusum"),
                          published=fng["published_at"].max()))
    return out


def _closes(source: str, symbol: str) -> pd.Series | None:
    """Daily dividend-adjusted closes from the lab's own data lake.

    Args:
        source: ``yahoo`` or ``binance``.
        symbol: Instrument.

    Returns:
        Closes by UTC day, or None when not ingested.
    """
    try:
        bars = adjusted(lake.read_series(source, symbol, "1d"))
    except FileNotFoundError:
        return None
    s = pd.Series(bars["close"].to_numpy(float), index=pd.to_datetime(bars["ts"], utc=True).dt.floor("D"))
    return s[~s.index.duplicated(keep="last")].sort_index()


def price_signals(wl: dict[str, Any]) -> list[Signal]:
    """Returns, realised volatility and cross-asset correlation from the lab's stored prices.

    Args:
        wl: The watchlist (its ``prices`` section).

    Returns:
        Two signals per instrument and one for the cross-asset correlation.
    """
    out = []
    closes: dict[str, pd.Series] = {}
    for source, symbols in wl["prices"].items():
        cat = "crypto" if source == "binance" else "stress"
        for sym in symbols:
            c = _closes(source, sym)
            if c is None or len(c) < MIN_HISTORY:
                continue
            closes[sym] = c
            r = np.log(c).diff()
            rv = np.sqrt((r**2).rolling(RV_WINDOW).mean() * (365 if source == "binance" else 252))
            out.append(Signal(f"prices/{sym}:return", f"{sym} daily return", cat, "both", 250, "daily log return",
                              "log return", r, r, detectors=("robust_z",)))
            out.append(Signal(f"prices/{sym}:rv", f"{sym} realised volatility ({RV_WINDOW} days, annualised)", cat,
                              "up", 250, f"log of {RV_WINDOW}-day realised volatility", "annualised",
                              np.log(rv.where(rv > 0)), rv))
    have = [s for s in CORR_SET if s in closes]
    if len(have) >= 3:
        rets = pd.DataFrame({s: np.log(closes[s]).diff() for s in have}).dropna()
        corr = rets.rolling(CORR_WINDOW).corr()
        avg = corr.groupby(level=0).apply(
            lambda m: m.to_numpy()[np.triu_indices(len(have), 1)].mean() if m.notna().all().all() else np.nan)
        out.append(Signal("prices/correlation", f"Average pairwise correlation of {', '.join(have)} "
                          f"({CORR_WINDOW} days)", "stress", "up", 250, "average pairwise correlation of daily returns",
                          "correlation", avg, avg))
    return out


def build(wl: dict[str, Any] | None = None) -> list[Signal]:
    """Every signal the radar can compute from what is stored.

    Args:
        wl: The watchlist (None loads it).

    Returns:
        Signals with missing values dropped (detectors run only on those with ``MIN_HISTORY`` observations).
    """
    wl = wl or watchlist.load()
    out = []
    for s in [*attention(wl), *scenario_signals(wl), *stress_signals(), *price_signals(wl)]:
        x = s.x.replace([np.inf, -np.inf], np.nan).dropna()
        if len(x) < 2:
            continue
        s.x, s.level = x, s.level.reindex(x.index)
        out.append(s)
    return out

