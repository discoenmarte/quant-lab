"""Synthetic multi-asset markets for calibrating the gauntlet on portfolio strategies.

Each asset's daily log return is a common market factor plus its own shock, both with GARCH(1,1) volatility and
Student-t tails, so assets are correlated, volatility clusters and crashes are shared. An optional hidden trend
per asset (a drift that flips sign at random, every six months on average, independently across assets) plants
an edge that diversified trend rules can earn; without it there is nothing to find.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..backtest.engine import COST_PRESETS
from .panel import Panel

DPY = 252


def _garch_path(rng: np.random.Generator, n: int, vol: float, a: float = 0.08, b: float = 0.90) -> np.ndarray:
    """Unit-scaled GARCH(1,1) shocks with Student-t(5) innovations, multiplied by their conditional volatility.

    Args:
        rng: Random generator.
        n: Number of days.
        vol: Long-run annual volatility.
        a: ARCH coefficient.
        b: GARCH coefficient.

    Returns:
        Daily shocks with long-run standard deviation ``vol / sqrt(252)``.
    """
    var_lr = vol**2 / DPY
    omega = var_lr * (1 - a - b)
    z = rng.standard_t(5, n) / math.sqrt(5 / 3)
    out = np.empty(n)
    v = var_lr
    for i in range(n):
        out[i] = math.sqrt(v) * z[i]
        v = omega + a * out[i] ** 2 + b * v
    return out


def synthetic_panel(
    years: float,
    n_assets: int = 8,
    trend: float = 0.0,
    seed: int = 0,
    corr: float = 0.3,
    vol: float = 0.16,
    drift: float = 0.04,
    duration: float = 126.0,
) -> Panel:
    """A panel of correlated GARCH assets with an optional hidden trend in each.

    Args:
        years: Length in years of 252 days.
        n_assets: Number of instruments.
        trend: Size of each asset's hidden trend, annual (0.2 = ±20% a year). 0 gives pure noise.
        seed: Random seed.
        corr: Share of each asset's variance that comes from the common factor.
        vol: Long-run annual volatility of each asset.
        drift: Unconditional annual drift of each asset.
        duration: Mean length of a trend regime in days.

    Returns:
        A panel with ETF costs and no risk-free rate, on business days from 1990-01-02.
    """
    rng = np.random.default_rng(seed)
    n = int(years * DPY)
    common = _garch_path(rng, n, vol)
    log_r = np.empty((n, n_assets))
    gaps = np.empty((n, n_assets))
    for j in range(n_assets):
        own = _garch_path(rng, n, vol)
        shock = math.sqrt(corr) * common + math.sqrt(1 - corr) * own
        flips = rng.random(n) < 1.0 / duration
        state = np.where(np.cumsum(flips) % 2 == 0, 1.0, -1.0) * (1.0 if rng.random() < 0.5 else -1.0)
        log_r[:, j] = drift / DPY + state * trend / DPY + shock
        gaps[:, j] = 0.3 * log_r[:, j] + rng.normal(0, 1, n) * np.abs(shock) * 0.2
    close = 100.0 * np.exp(np.cumsum(log_r, axis=0))
    open_ = close / np.exp(log_r - gaps)
    ts = pd.bdate_range("1990-01-02", periods=n, tz="UTC")
    symbols = [f"SYN{j}" for j in range(n_assets)]
    costs = np.full(n_assets, COST_PRESETS["etf"].per_unit)
    return Panel("synthetic", ts, symbols, ["synthetic"] * n_assets, ["equity"] * n_assets, open_, close,
                 np.zeros(n), costs)
