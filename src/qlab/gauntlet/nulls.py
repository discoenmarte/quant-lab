"""Null worlds: shuffled price histories for the permutation test, and resampled futures for risk.

The permutation test (after Masters, 2020) asks: does the strategy do better on the real history than on
histories with the same bars in a random order? Shuffling keeps every bar's shape and roughly the overall drift
but destroys trends, cycles and regimes, which is exactly what a timing strategy claims to exploit.

A plain shuffle also destroys volatility clustering, and that makes the test too lenient: calibration on GARCH
markets with no edge passed a 200-day trend rule 8% of the time at p <= 0.05. So bars are shuffled in units of
their local volatility and put back at the volatility of the place they land (a studentised permutation). The
null world keeps the real calm and turbulent stretches; only the direction and order of the moves are random.

The stationary bootstrap (Politis & Romano, 1994) resamples blocks of the strategy's own returns to show the
range of drawdowns and growth it could produce. It measures risk; it is not evidence of an edge.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from ..backtest import metrics
from ..data.bars import adjusted

EWMA_LAMBDA = 0.94  # RiskMetrics daily decay


def local_vol(log_returns: np.ndarray, lam: float = EWMA_LAMBDA) -> np.ndarray:
    """Volatility forecast per period from an exponentially weighted average of past squared returns.

    Args:
        log_returns: Log returns per period.
        lam: Decay; 0.94 is the RiskMetrics daily value (an effective window of about 16 days).

    Returns:
        Positive volatility per period, each using only earlier returns (the first uses the first 20).
    """
    r = np.nan_to_num(np.asarray(log_returns, dtype=float))
    var = np.empty(len(r))
    v = float(np.mean(r[:20] ** 2)) if len(r) else 0.0
    for i, x in enumerate(r):
        var[i] = v
        v = lam * v + (1 - lam) * x * x
    floor = max(float(np.median(var)) * 1e-4, 1e-16)
    return np.sqrt(np.maximum(var, floor))


def permuter(bars: pd.DataFrame, keep_vol: bool = True) -> Callable[[np.random.Generator], pd.DataFrame]:
    """Prepare a history for shuffling and return a function that draws one shuffled copy per call.

    Each bar is described by its overnight gap, its open-to-close move and its high/low excursions, all in log
    terms. With ``keep_vol`` those are divided by the bar's local volatility, shuffled together (bar 0 stays
    first), multiplied by the local volatility at their new position, and chained from the first close. Shapes
    come from dividend-adjusted prices, so dividends are part of the drift. The decomposition and the volatility
    path are computed once, so each draw costs one shuffle.

    Args:
        bars: Normalised bars with a positional index.
        keep_vol: Keep the real volatility path (studentised shuffle). False shuffles raw bars, which keeps the
            total return exactly but loses volatility clustering.

    Returns:
        ``draw(rng)`` giving bars with the same timestamps, in random order. ``adj_close`` equals ``close``,
        since the adjustment is already in the prices.
    """
    base = adjusted(bars)
    o, h, low, c = (base[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    parts = np.vstack([
        np.log(o[1:] / c[:-1]),
        np.log(c[1:] / o[1:]),
        np.log(h[1:] / np.maximum(o[1:], c[1:])),
        np.log(low[1:] / np.minimum(o[1:], c[1:])),
    ])
    vol = local_vol(parts[0] + parts[1]) if keep_vol else np.ones(parts.shape[1])
    unit = parts / vol

    def draw(rng: np.random.Generator) -> pd.DataFrame:
        gap, body, up, down = np.exp(unit[:, rng.permutation(unit.shape[1])] * vol)
        opens = c[0] * np.cumprod(gap * np.concatenate([[1.0], body[:-1]]))
        closes = opens * body
        out = base.copy()
        out["open"] = np.concatenate([[o[0]], opens])
        out["close"] = np.concatenate([[c[0]], closes])
        out["high"] = np.concatenate([[h[0]], np.maximum(opens, closes) * up])
        out["low"] = np.concatenate([[low[0]], np.minimum(opens, closes) * down])
        if "adj_close" in out:
            out["adj_close"] = out["close"]
        return out

    return draw


def permuted_bars(bars: pd.DataFrame, rng: np.random.Generator, keep_vol: bool = True) -> pd.DataFrame:
    """One shuffled copy of a history (see ``permuter``, which is faster for many draws).

    Args:
        bars: Normalised bars with a positional index.
        rng: Random generator.
        keep_vol: Keep the real volatility path.

    Returns:
        Bars with the same timestamps, in random order.
    """
    return permuter(bars, keep_vol)(rng)


def stationary_bootstrap(
    r: np.ndarray,
    n_paths: int = 1000,
    block: float = 20.0,
    seed: int = 3,
    ppy: int = 252,
    dd_budget: float = 0.25,
) -> dict[str, Any]:
    """Resample a return series in random-length blocks and summarise the futures it could produce.

    Blocks keep short-range dependence (volatility clusters, trend persistence) that a plain bootstrap would
    break. The result sizes the strategy: ``size_for_budget`` is the fraction of capital at which the
    95th-percentile drawdown just fits the budget.

    Args:
        r: Per-period returns.
        n_paths: Number of resampled paths.
        block: Mean block length in periods (geometric distribution).
        seed: Random seed.
        ppy: Periods per year, for CAGR.
        dd_budget: Drawdown the sizing aims to keep the 95th percentile within, e.g. 0.25.

    Returns:
        ``max_dd`` and ``cagr`` percentiles (p5, p50, p95), ``max_dd_samples`` (for a histogram), ``fan``
        (equity percentiles p5/p25/p50/p75/p95 at up to 200 evenly spaced steps), ``realised_max_dd`` and
        ``size_for_budget`` (capped at 2.0).
    """
    rng = np.random.default_rng(seed)
    r = np.nan_to_num(np.asarray(r, dtype=float))
    t = len(r)
    idx = np.empty((n_paths, t), dtype=np.int32)
    idx[:, 0] = rng.integers(0, t, n_paths)
    jumps = rng.random((n_paths, t), dtype=np.float32) < 1.0 / block
    starts = rng.integers(0, t, (n_paths, t), dtype=np.int32)
    for i in range(1, t):
        idx[:, i] = np.where(jumps[:, i], starts[:, i], (idx[:, i - 1] + 1) % t)
    paths = r[idx]

    def p95_dd(scale: float) -> float:
        eq = np.cumprod(1.0 + scale * paths, axis=1)
        return float(np.percentile((1.0 - eq / np.maximum.accumulate(eq, axis=1)).max(axis=1), 95))

    eq = np.cumprod(1.0 + paths, axis=1)
    max_dd = (1.0 - eq / np.maximum.accumulate(eq, axis=1)).max(axis=1)
    years = t / ppy
    cagr = np.where(eq[:, -1] > 0, np.maximum(eq[:, -1], 1e-12) ** (1.0 / years) - 1.0, -1.0)
    steps = np.unique(np.linspace(0, t - 1, min(200, t)).astype(int))
    q = np.percentile(eq[:, steps], [5, 25, 50, 75, 95], axis=0)

    # Bisection on the position scale; drawdown grows monotonically with it.
    lo, hi = 0.0, 2.0
    if p95_dd(hi) <= dd_budget:
        size = hi
    else:
        for _ in range(12):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if p95_dd(mid) <= dd_budget else (lo, mid)
        size = lo
    return {
        "max_dd": dict(zip(("p5", "p50", "p95"), np.percentile(max_dd, [5, 50, 95]).tolist(), strict=True)),
        "cagr": dict(zip(("p5", "p50", "p95"), np.percentile(cagr, [5, 50, 95]).tolist(), strict=True)),
        "max_dd_samples": np.round(max_dd, 4).tolist(),
        "fan": {"step": steps.tolist(), **{f"p{p}": q[i].tolist() for i, p in enumerate((5, 25, 50, 75, 95))}},
        "realised_max_dd": metrics.max_drawdown(r),
        "size_for_budget": size,
        "dd_budget": dd_budget,
    }
