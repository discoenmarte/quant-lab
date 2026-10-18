"""Synthetic markets for calibrating the gauntlet on portfolio strategies.

``synthetic_panel`` (M3b): each asset's daily log return is a common market factor plus its own shock, both
with GARCH(1,1) volatility and Student-t tails, so assets are correlated, volatility clusters and crashes are
shared. An optional hidden trend per asset (a drift that flips sign at random, every six months on average,
independently across assets) plants an edge that diversified trend rules can earn; without it there is nothing
to find.

``synthetic_crypto_panel`` (M3c): a crypto-like cross-section where coins list and delist at random, trade
every calendar day, differ in volatility and size, and enter the universe by trailing dollar volume. Its noise
version has zero expected arithmetic return for every coin, so every long-only, fully invested rule has an
expected edge of exactly zero against equal weight (``E[r_p] − β·E[r_b] = 0 − β·0``), whatever it tilts toward:
volatile coins, small ones or recent losers. That makes even the low-volatility rule calibratable, which a
drift defined in log terms would not allow (volatile coins would then earn more arithmetically). Edges can be
planted: a hidden trend per coin (momentum), or a premium for small dollar volume (size).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..backtest.engine import COST_PRESETS
from .panel import Membership, Panel, membership

DPY = 252
CRYPTO_DPY = 365


def _garch_path(rng: np.random.Generator, n: int, vol: float, a: float = 0.08, b: float = 0.90,
                dpy: int = DPY) -> np.ndarray:
    """Unit-scaled GARCH(1,1) shocks with Student-t(5) innovations, multiplied by their conditional volatility.

    Args:
        rng: Random generator.
        n: Number of days.
        vol: Long-run annual volatility.
        a: ARCH coefficient.
        b: GARCH coefficient.
        dpy: Days per year.

    Returns:
        Daily shocks with long-run standard deviation ``vol / sqrt(dpy)``.
    """
    var_lr = vol**2 / dpy
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


def synthetic_crypto_panel(
    years: float,
    n_assets: int = 100,
    top: int = 30,
    trend: float = 0.0,
    size_premium: float = 0.0,
    seed: int = 0,
    corr: float = 0.4,
    vol: float = 0.8,
    listed_at_start: float = 0.3,
    delist_rate: float = 0.12,
    duration: float = 126.0,
) -> Panel:
    """A crypto-like cross-section where coins list and delist, with point-in-time membership by dollar volume.

    Each coin's daily arithmetic return is ``s_t·trend/365 + premium_t + σ_t ε_t``: ``ε_t`` mixes a common GARCH
    factor with the coin's own GARCH shock (Student t, 5 degrees of freedom), ``σ`` is the coin's long-run
    volatility (log-normal across coins around ``vol``), ``s_t`` is a hidden ±1 regime that flips every
    ``duration`` days on average, and ``premium_t`` pays small coins: ``size_premium/365`` times minus the
    coin's standardised log dollar volume that day. Returns are floored at −90% a day. Log dollar volume follows
    a persistent AR(1) around a level that differs across coins, plus a common activity factor, independent of
    returns. A share of coins trade from the first day; the rest list at random dates in the first 80% of the
    period; each listed coin delists with probability ``delist_rate`` a year and never returns. Prices open at
    the previous close, as on a 24-hour exchange.

    Args:
        years: Length in years of 365 days.
        n_assets: Coins that ever trade.
        top: Universe size (members by 30-day dollar volume).
        trend: Size of each coin's hidden trend, annual (0 gives no momentum).
        size_premium: Annual return per standard deviation of smallness (0 gives no size effect).
        seed: Random seed.
        corr: Share of each coin's variance from the common factor.
        vol: Median long-run annual volatility.
        listed_at_start: Share of coins trading from the first day.
        delist_rate: Yearly probability that a listed coin delists.
        duration: Mean length of a trend regime, days.

    Returns:
        A dynamic panel with crypto costs (``crypto_xs``), drifting positions and no risk-free rate, starting on
        the first day with ``min(20, top)`` members (dates from 2015-01-01 on).
    """
    rng = np.random.default_rng(seed)
    n = int(years * CRYPTO_DPY) + 60  # room for the 30-day membership warm-up and the start rule
    common = _garch_path(rng, n, 1.0, dpy=CRYPTO_DPY) / math.sqrt(1.0 / CRYPTO_DPY)  # unit daily variance
    vols = np.clip(np.exp(rng.normal(math.log(vol), 0.35, n_assets)), 0.3, 2.5)
    level = rng.normal(math.log(5e6), 1.5, n_assets)
    activity = np.zeros(n)
    for t in range(1, n):
        activity[t] = 0.99 * activity[t - 1] + rng.normal(0, 0.05)
    start = np.where(rng.random(n_assets) < listed_at_start, 0, rng.integers(0, int(0.8 * n), n_assets))
    life = rng.exponential(CRYPTO_DPY / max(delist_rate, 1e-9), n_assets)
    end = np.minimum(start + np.maximum(life.astype(int), 60), n)
    days = np.arange(n)[:, None]
    avail = (days >= start) & (days < end)

    log_dv = np.empty((n, n_assets))
    shocks = np.empty((n, n_assets))
    state = np.empty((n, n_assets))
    for j in range(n_assets):
        own = _garch_path(rng, n, 1.0, dpy=CRYPTO_DPY) / math.sqrt(1.0 / CRYPTO_DPY)
        shocks[:, j] = (math.sqrt(corr) * common + math.sqrt(1 - corr) * own) * vols[j] / math.sqrt(CRYPTO_DPY)
        flips = rng.random(n) < 1.0 / duration
        state[:, j] = np.where(np.cumsum(flips) % 2 == 0, 1.0, -1.0) * (1.0 if rng.random() < 0.5 else -1.0)
        x = np.empty(n)
        x[0] = rng.normal(0, 0.7)
        e = rng.normal(0, 0.1, n)
        for t in range(1, n):
            x[t] = 0.98 * x[t - 1] + e[t]
        log_dv[:, j] = level[j] + x + activity
    premium = np.zeros((n, n_assets))
    if size_premium:
        live = np.where(avail, log_dv, np.nan)
        with np.errstate(invalid="ignore"):
            z = (live - np.nanmean(live, axis=1, keepdims=True)) / np.nanstd(live, axis=1, keepdims=True)
        premium = np.nan_to_num(-z) * size_premium / CRYPTO_DPY
    r = np.maximum(state * trend / CRYPTO_DPY + premium + shocks, -0.9)
    r = np.where(avail, r, 0.0)
    growth = np.cumprod(np.where(avail, 1.0 + r, 1.0), axis=0)
    first_growth = growth[np.clip(start, 0, n - 1), np.arange(n_assets)] / (1.0 + r[np.clip(start, 0, n - 1),
                                                                               np.arange(n_assets)])
    close = np.where(avail, 100.0 * growth / first_growth, np.nan)
    open_ = np.full_like(close, np.nan)
    open_[1:] = np.where(avail[1:] & avail[:-1], close[:-1], np.nan)
    open_ = np.where(avail & np.isnan(open_), 100.0, open_)  # listing day opens at 100
    dv = np.where(avail, np.exp(log_dv), np.nan)

    rule = Membership(top, 30, min(20, top))
    member = membership(dv, avail, rule.top, rule.window)
    counts = member.sum(axis=1)
    first = int(np.argmax(counts >= rule.min_members)) if (counts >= rule.min_members).any() else 0
    rows = slice(first, None)
    ts = pd.date_range("2015-01-01", periods=n, freq="D", tz="UTC")[rows]
    costs = np.full(n_assets, COST_PRESETS["crypto_xs"].per_unit)
    return Panel("synthetic_crypto", ts, [f"C{j:03d}" for j in range(n_assets)], ["synthetic"] * n_assets,
                 ["crypto"] * n_assets, open_[rows], close[rows], np.zeros(len(ts)), costs, avail[rows],
                 member[rows], dv[rows], rule, drift=True)
