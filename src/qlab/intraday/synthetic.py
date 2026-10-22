"""Synthetic hourly crypto markets for calibrating the gauntlet on intraday rules.

Like the daily calibration markets (``gauntlet.calibration.synthetic_bars``), noise markets have volatility clustering
(GARCH on the daily variance) and fat tails (Student t with five degrees of freedom), and nothing a rule can exploit.
They add what matters within a day: an intraday volatility profile peaking in the US afternoon (15:00 UTC), as in
real Bitcoin trading. Planted markets add intraday momentum: the day's last hour drifts in the direction of its
first hour, by ``momentum`` times the last hour's standard deviation.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..data.bars import normalize_bars

HOURS = 24
DAYS_PER_YEAR = 365


def profile() -> np.ndarray:
    """Relative variance of each UTC hour, averaging one.

    Returns:
        24 weights, highest at 15:00 UTC and lowest at 03:00 UTC (ratio 3).
    """
    w = 1.0 + 0.5 * np.cos(2 * math.pi * (np.arange(HOURS) - 15) / HOURS)
    return w / w.mean()


def synthetic_hourly(years: float, momentum: float = 0.0, seed: int = 0, vol: float = 0.6,
                     drift: float = 0.3) -> pd.DataFrame:
    """Hourly bars around the clock from a GARCH market with an intraday volatility profile.

    Args:
        years: Length in years of 365 days.
        momentum: Planted intraday momentum: the last hour's expected return is this many of its standard deviations
            in the direction of the day's first hour. 0 gives a noise market.
        seed: Random seed.
        vol: Long-run annual volatility.
        drift: Unconditional annual drift.

    Returns:
        Normalised hourly bars from 2010-01-01 00:00 UTC.
    """
    rng = np.random.default_rng(seed)
    n_days = int(years * DAYS_PER_YEAR)
    a, b = 0.08, 0.90
    var_lr = vol**2 / DAYS_PER_YEAR
    omega = var_lr * (1 - a - b)
    day_shock = rng.standard_t(5, n_days) / math.sqrt(5 / 3)
    var = np.empty(n_days)
    v = var_lr
    for i in range(n_days):
        var[i] = v
        v = omega + a * v * day_shock[i] ** 2 + b * v
    sd = np.sqrt(var[:, None] / HOURS * profile()[None, :])  # days × hours
    eps = rng.standard_t(5, (n_days, HOURS)) / math.sqrt(5 / 3)
    r = drift / DAYS_PER_YEAR / HOURS + sd * eps
    if momentum:
        r[:, -1] += momentum * sd[:, -1] * np.sign(r[:, 0])
    log_r = r.ravel()
    close = 100.0 * np.exp(np.cumsum(log_r))
    open_ = np.concatenate([[100.0], close[:-1]])
    s = sd.ravel()
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.4, len(close))) * s)
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.4, len(close))) * s)
    ts = pd.date_range("2010-01-01", periods=len(close), freq="h", tz="UTC")
    return normalize_bars(pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                                        "volume": 1e6}))
