"""Performance metrics on a series of periodic returns.

Conventions: returns are simple (not log) returns per period. ``max_dd`` is a positive fraction, so 0.35
means the worst fall from a peak was 35%.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def sharpe(r: pd.Series | np.ndarray, rf: pd.Series | np.ndarray | None = None, ppy: int = 252) -> float:
    """Annualised Sharpe ratio of periodic returns.

    Args:
        r: Simple returns per period.
        rf: Risk-free return per period, aligned with ``r``. None means zero.
        ppy: Periods per year: 252 for stock days, 365 for crypto days.

    Returns:
        Mean excess return divided by its standard deviation, times sqrt(ppy). NaN when there are fewer than
        two observations or no volatility.
    """
    a = np.asarray(r, dtype=float)
    if rf is not None:
        a = a - np.asarray(rf, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        return float("nan")
    sd = a.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(a.mean() / sd * math.sqrt(ppy))


def sortino(r: pd.Series | np.ndarray, ppy: int = 252) -> float:
    """Annualised Sortino ratio: mean return over downside deviation.

    Args:
        r: Simple returns per period.
        ppy: Periods per year.

    Returns:
        The ratio, or NaN with fewer than two negative periods.
    """
    a = np.asarray(r, dtype=float)
    a = a[np.isfinite(a)]
    downside = a[a < 0]
    if len(a) < 2 or len(downside) < 2:
        return float("nan")
    dd = math.sqrt(float(np.mean(downside**2)))
    return float(a.mean() / dd * math.sqrt(ppy)) if dd > 0 else float("nan")


def equity_curve(r: pd.Series | np.ndarray, start: float = 1.0) -> np.ndarray:
    """Compounded value of an investment over time.

    Args:
        r: Simple returns per period. NaN counts as a zero return.
        start: Starting value.

    Returns:
        Array of the same length as ``r``: value after each period.
    """
    return start * np.cumprod(1.0 + np.nan_to_num(np.asarray(r, dtype=float)))


def cagr(r: pd.Series | np.ndarray, ppy: int = 252) -> float:
    """Compound annual growth rate.

    Args:
        r: Simple returns per period.
        ppy: Periods per year.

    Returns:
        Yearly rate that compounds to the same final value. -1.0 if the investment was wiped out, NaN for an
        empty series.
    """
    a = np.nan_to_num(np.asarray(r, dtype=float))
    if len(a) == 0:
        return float("nan")
    growth = float(np.prod(1.0 + a))
    years = len(a) / ppy
    if growth <= 0:
        return -1.0
    return growth ** (1.0 / years) - 1.0


def drawdown_series(r: pd.Series | np.ndarray) -> np.ndarray:
    """Distance below the running peak after each period.

    Args:
        r: Simple returns per period.

    Returns:
        Array of values <= 0; -0.2 means 20% below the previous peak.
    """
    eq = equity_curve(r)
    peak = np.maximum.accumulate(eq)
    return eq / peak - 1.0


def max_drawdown(r: pd.Series | np.ndarray) -> float:
    """Worst fall from a peak.

    Args:
        r: Simple returns per period.

    Returns:
        Positive fraction (0.35 = 35%). NaN for an empty series.
    """
    dd = drawdown_series(r)
    return float(-dd.min()) if len(dd) else float("nan")


def summary(
    r: pd.Series,
    rf: pd.Series | None = None,
    ppy: int = 252,
    positions: pd.Series | None = None,
) -> dict[str, float | int]:
    """Headline numbers for one return series.

    Args:
        r: Simple returns per period.
        rf: Risk-free return per period for the Sharpe ratio. None means zero.
        ppy: Periods per year.
        positions: Position held each period (-1..1). When given, exposure and turnover are added.

    Returns:
        Dict with ``n_obs, total_return, cagr, vol, sharpe, sortino, max_dd, calmar, skew, kurtosis`` and,
        with positions, ``exposure`` (share of periods invested) and ``turnover`` (position change per year).
        Kurtosis is raw (3.0 for a normal distribution), as the Deflated Sharpe Ratio expects.
    """
    a = np.asarray(r, dtype=float)
    finite = a[np.isfinite(a)]
    out: dict[str, float | int] = {
        "n_obs": int(len(finite)),
        "total_return": float(np.prod(1.0 + finite) - 1.0),
        "cagr": cagr(a, ppy),
        "vol": float(finite.std(ddof=1) * math.sqrt(ppy)) if len(finite) > 1 else float("nan"),
        "sharpe": sharpe(a, rf, ppy),
        "sortino": sortino(a, ppy),
        "max_dd": max_drawdown(a),
        "skew": float(pd.Series(finite).skew()) if len(finite) > 2 else float("nan"),
        "kurtosis": float(pd.Series(finite).kurt() + 3.0) if len(finite) > 3 else float("nan"),
    }
    mdd = out["max_dd"]
    out["calmar"] = out["cagr"] / mdd if mdd and mdd > 0 else float("nan")
    if positions is not None:
        p = np.asarray(positions, dtype=float)
        out["exposure"] = float(np.mean(np.abs(p) > 1e-9))
        years = max(len(p) / ppy, 1e-9)
        out["turnover"] = float(np.nansum(np.abs(np.diff(p, prepend=0.0))) / years)
    return out


def monthly_returns(r: pd.Series) -> list[dict[str, float | int]]:
    """Compounded return for each calendar month.

    Args:
        r: Simple returns indexed by date.

    Returns:
        Records ``{"year", "month", "ret"}`` in date order.

    Raises:
        ValueError: The index is not a DatetimeIndex.
    """
    if not isinstance(r.index, pd.DatetimeIndex):
        raise ValueError("monthly_returns needs a DatetimeIndex")
    grouped = (1.0 + r.fillna(0.0)).groupby([r.index.year, r.index.month]).prod() - 1.0
    return [{"year": int(y), "month": int(m), "ret": float(v)} for (y, m), v in grouped.items()]
