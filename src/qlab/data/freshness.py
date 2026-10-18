"""Data freshness: how far each source's stored bars are behind the latest bar the world has already finished.

Design (decision 0008):

- **Against the calendar, not the clock.** The latest bar that should exist is the last session whose close is final
  at the time of the check (``paper.sessions``): weekdays for US ETFs (final 30 minutes after the 16:00 New York
  close), every day for Binance (a day's candle closes at 00:00 UTC). Lag is counted in sessions.
- **The laggard decides.** A source is as fresh as its stalest default symbol, since one missing ETF is enough to
  stop a portfolio decision.
- **Tolerances absorb what isn't modelled,** so a warning means something is wrong:

  - US ETFs: 1 session. Exchange holidays aren't in the calendar, so a holiday looks like one missing session.
  - Binance: 2 days. The public archive publishes a day's file the day after, so one day of lag is normal.
  - Ken French factors: 95 days. The library is updated about monthly with data through an earlier month-end.

- A failed last ingest is reported beside the lag but isn't a freshness failure by itself: the nightly job's own
  failure alert covers it, and the data may still be inside its tolerance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from .. import db
from ..timeutil import now_utc
from .ingest import SOURCES, ensure_sources

TOLERANCE = {"equity": 1, "crypto": 2, "factors": 95}
UNIT = {"equity": "sessions", "crypto": "days", "factors": "days"}


def expected_latest(asset_class: str, interval: str, now: datetime) -> pd.Timestamp | None:
    """The newest bar that should be stored at a given time.

    Args:
        asset_class: ``"equity"``, ``"crypto"`` or ``"factors"``.
        interval: ``"1d"`` or ``"1h"``.
        now: Tz-aware time of the check.

    Returns:
        The bar's timestamp (UTC, tz-aware): a session date at 00:00 for daily bars; for hourly bars the last hour of
        that session, since the archive publishes whole days. None for factors, which have no session calendar.
    """
    from ..paper import sessions  # session times belong to the paper book; imported here to keep layers one-way

    t = pd.Timestamp(now).tz_convert("UTC")
    if asset_class == "factors":
        return None
    freq = "D" if asset_class == "crypto" else "B"
    days = pd.date_range((t - pd.Timedelta(days=10)).normalize(), t.normalize(), freq=freq, tz="UTC")
    done = days[sessions.finals(days, asset_class) <= t]
    if not len(done):
        return None
    return done[-1] + pd.Timedelta(hours=23) if interval == "1h" else done[-1]


def lag(asset_class: str, interval: str, last: pd.Timestamp, expected: pd.Timestamp | None, now: datetime) -> float:
    """How far a stored bar is behind the expected one.

    Args:
        asset_class: ``"equity"``, ``"crypto"`` or ``"factors"``.
        interval: ``"1d"`` or ``"1h"``.
        last: Timestamp of the stalest stored bar (UTC, tz-aware).
        expected: Output of ``expected_latest``.
        now: Tz-aware time of the check.

    Returns:
        Sessions behind for equities (weekdays), days behind for crypto and calendar days since the last value for
        factors. Zero or less means up to date.
    """
    if asset_class == "factors" or expected is None:
        return float((pd.Timestamp(now).tz_convert("UTC").normalize() - last.normalize()).days)
    if asset_class == "equity":
        return float(len(pd.bdate_range(last + pd.Timedelta(days=1), expected))) if expected > last else 0.0
    return float((expected.normalize() - last.normalize()).days)


def check(now: datetime | None = None) -> list[dict[str, Any]]:
    """Freshness of every source and interval the lab ingests by default.

    Args:
        now: Tz-aware time of the check; None means now.

    Returns:
        One row per source and interval: ``source, interval, asset_class``, ``status`` and ``last_success`` of the
        last ingest, ``n_series`` (default symbols stored), ``missing`` (default symbols not stored), ``newest`` and
        ``oldest`` (ISO dates or times of the newest and stalest symbol's last bar), ``laggard`` (that symbol),
        ``expected``, ``behind``, ``tolerance``, ``unit``, ``stale`` (behind beyond tolerance, or nothing stored) and
        ``message`` in plain words.
    """
    now = now or now_utc()
    ensure_sources()
    meta = {r["name"]: r for r in db.rows("SELECT name, status, last_success FROM sources")}
    out = []
    for name, info in SOURCES.items():
        for interval in info.intervals:
            series = db.rows("SELECT symbol, last_ts FROM series WHERE source = ? AND interval = ?", [name, interval])
            stored = {r["symbol"]: pd.Timestamp(r["last_ts"], tz="UTC") for r in series if r["symbol"] in info.symbols}
            missing = [s for s in info.symbols if s not in stored]
            ac, src = info.asset_class, meta.get(name) or {}
            row: dict[str, Any] = {
                "source": name, "interval": interval, "asset_class": ac,
                "status": src.get("status"), "last_success": src.get("last_success"),
                "n_series": len(stored), "missing": missing, "newest": None, "oldest": None, "laggard": None,
                "expected": None, "behind": None, "tolerance": TOLERANCE[ac], "unit": UNIT[ac],
            }
            expected = expected_latest(ac, interval, now)
            fmt = (lambda t: t.strftime("%Y-%m-%d %H:%M")) if interval == "1h" else (lambda t: str(t.date()))
            row["expected"] = fmt(expected) if expected is not None else None
            if not stored:
                row.update(stale=True, message=f"{name} {interval}: nothing stored yet; run an ingest.")
                out.append(row)
                continue
            laggard = min(stored, key=lambda s: stored[s])
            behind = lag(ac, interval, stored[laggard], expected, now)
            row.update(newest=fmt(max(stored.values())), oldest=fmt(stored[laggard]), laggard=laggard,
                       behind=round(behind, 2), stale=behind > TOLERANCE[ac] or bool(missing))
            if missing:
                row["message"] = (f"{name} {interval}: {len(missing)} default symbols missing "
                                  f"({', '.join(missing[:4])}).")
            elif ac == "factors":
                row["message"] = (f"{name} {interval}: last value {row['oldest']}, {behind:g} days old (published with "
                                  f"a lag; tolerance {TOLERANCE[ac]}).")
            elif behind > 0:
                unit = UNIT[ac][:-1] if behind == 1 else UNIT[ac]
                row["message"] = (f"{name} {interval}: {laggard} is {behind:g} {unit} behind (last bar "
                                  f"{row['oldest']}, expected {row['expected']}; tolerance {TOLERANCE[ac]}).")
            else:
                row["message"] = f"{name} {interval}: up to date through {row['oldest']}."
            out.append(row)
    return out
