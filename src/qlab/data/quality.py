"""Data quality checks run on every ingest.

Errors mean the data is wrong (non-positive prices, impossible OHLC). Warnings mean it needs a look (large
moves, gaps, stale prices, zero volume); many warnings are real market events, such as an exchange outage.
Each check records at most ``MAX_PER_CHECK`` examples plus a summary line with the total.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .. import db
from ..timeutil import naive_utc
from .bars import PRICE_COLUMNS

MAX_PER_CHECK = 25

LIMITS = {
    "equity": {"max_abs_return": 0.25, "max_gap_days": 5, "stale_run": 5},
    "crypto": {"max_abs_return": 0.50, "max_gap_days": 1, "stale_run": 24},
}


@dataclass(frozen=True)
class Issue:
    """One quality finding.

    Attributes:
        check: Check name, e.g. ``"gap"``.
        severity: ``"error"`` or ``"warn"``.
        ts: Bar the finding refers to; None for series-level findings.
        detail: Human-readable explanation with the numbers.
    """

    check: str
    severity: str
    ts: pd.Timestamp | None
    detail: str


def _capped(check: str, severity: str, rows: pd.DataFrame, describe) -> list[Issue]:
    """Turn flagged rows into issues, keeping at most ``MAX_PER_CHECK`` examples.

    Args:
        check: Check name.
        severity: ``"error"`` or ``"warn"``.
        rows: Flagged rows, each with a ``ts`` column.
        describe: Function from a row to its detail text.

    Returns:
        Issues for the first examples plus one summary issue when more were flagged.
    """
    issues = [Issue(check, severity, r.ts, describe(r)) for r in rows.head(MAX_PER_CHECK).itertuples()]
    if len(rows) > MAX_PER_CHECK:
        issues.append(Issue(check, severity, None, f"{len(rows)} bars flagged in total"))
    return issues


def check_bars(df: pd.DataFrame, *, asset_class: str, interval: str) -> list[Issue]:
    """Run every bar check.

    Args:
        df: Normalised bars.
        asset_class: ``"equity"`` or ``"crypto"``; selects the limits.
        interval: ``"1d"`` or an intraday interval such as ``"1h"``.

    Returns:
        All issues found, errors first.
    """
    lim = LIMITS[asset_class]
    issues: list[Issue] = []
    if df.empty:
        return [Issue("empty", "error", None, "series has no rows")]

    nonpos = df[(df[PRICE_COLUMNS] <= 0).any(axis=1)]
    issues += _capped("non_positive_price", "error", nonpos, lambda r: f"price <= 0 (close {r.close})")

    body_low = df[["open", "close"]].min(axis=1)
    body_high = df[["open", "close"]].max(axis=1)
    bad = df[(df["low"] > body_low * (1 + 1e-9)) | (df["high"] < body_high * (1 - 1e-9))]
    issues += _capped(
        "ohlc_inconsistent", "error", bad, lambda r: f"low {r.low} / high {r.high} outside open-close body"
    )

    ret = df["close"].pct_change()
    big = df.assign(ret=ret)[ret.abs() > lim["max_abs_return"]]
    issues += _capped("extreme_return", "warn", big, lambda r: f"close-to-close move {r.ret:+.1%}")

    step = pd.Timedelta(days=1) if interval == "1d" else pd.Timedelta(interval)
    gaps = df.assign(gap=df["ts"].diff())
    if asset_class == "equity" and interval == "1d":
        flagged = gaps[gaps["gap"] > pd.Timedelta(days=lim["max_gap_days"])]
    else:
        flagged = gaps[gaps["gap"] > step]
    issues += _capped("gap", "warn", flagged, lambda r: f"{r.gap} since previous bar")

    unchanged = df["close"].eq(df["close"].shift())
    run_id = (~unchanged).cumsum()
    run_len = unchanged.groupby(run_id).cumsum()
    stale = df[run_len == lim["stale_run"]]
    issues += _capped("stale_price", "warn", stale, lambda r: f"close unchanged for {lim['stale_run']} bars")

    if "volume" in df:
        zero = df[df["volume"] == 0]
        issues += _capped("zero_volume", "warn", zero, lambda r: "volume is zero")

    return sorted(issues, key=lambda i: i.severity != "error")


def check_factors(df: pd.DataFrame) -> list[Issue]:
    """Sanity checks for the daily factor file.

    Args:
        df: Parsed factors with ``ts`` and ``rf`` as fractions.

    Returns:
        Issues found: a daily risk-free rate outside ±1%, or gaps longer than five days.
    """
    issues: list[Issue] = []
    odd = df[df["rf"].abs() > 0.01]
    issues += _capped("rf_out_of_range", "error", odd, lambda r: f"daily RF {r.rf:.4f}")
    gaps = df.assign(gap=df["ts"].diff())
    issues += _capped("gap", "warn", gaps[gaps["gap"] > pd.Timedelta(days=5)], lambda r: f"{r.gap} gap")
    return issues


def record_issues(source: str, symbol: str, interval: str, issues: list[Issue]) -> None:
    """Replace a series' stored issues with a fresh set.

    Args:
        source: Source name.
        symbol: Instrument.
        interval: Bar interval.
        issues: Findings from the latest check.
    """
    found = naive_utc()
    with db.connect() as con:
        con.execute(
            "DELETE FROM quality_issues WHERE source = ? AND symbol = ? AND interval = ?",
            [source, symbol, interval],
        )
        if not issues:  # DuckDB rejects executemany with no rows
            return
        con.executemany(
            """INSERT INTO quality_issues (source, symbol, interval, ts, check_name, severity, detail, found_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                [source, symbol, interval,
                 i.ts.to_pydatetime().replace(tzinfo=None) if i.ts is not None else None,
                 i.check, i.severity, i.detail, found]
                for i in issues
            ],
        )
