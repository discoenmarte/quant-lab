"""Probability of Backtest Overfitting via combinatorially symmetric cross-validation (CSCV).

Bailey, Borwein, López de Prado & Zhu (2017). Split the return history into S equal blocks. For each way of
choosing S/2 blocks as in-sample, pick the configuration with the best in-sample Sharpe and find where it
ranks out of sample. PBO is the share of splits where the in-sample winner lands at or below the
out-of-sample median. 0.5 means picking the winner is no better than a coin flip.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np


def pbo(returns: np.ndarray, n_blocks: int = 16, max_splits: int = 2000, seed: int = 11) -> dict[str, Any]:
    """Estimate PBO for a set of strategy configurations.

    Args:
        returns: Matrix of per-period returns, shape ``(T, N)``: one column per configuration, same periods.
        n_blocks: Number of blocks S (even). 16 gives 12,870 splits.
        max_splits: Use a random subset of splits above this number, for speed.
        seed: Random seed for the subset.

    Returns:
        ``pbo`` (probability), ``logits`` (one per split), ``is_sharpe`` and ``oos_sharpe`` of the in-sample
        winner per split (for the degradation scatter), and ``n_splits``. ``pbo`` is NaN with fewer than two
        configurations.

    Raises:
        ValueError: ``n_blocks`` is odd or larger than the number of periods.
    """
    t, n = returns.shape
    if n < 2:
        return {"pbo": float("nan"), "logits": [], "is_sharpe": [], "oos_sharpe": [], "n_splits": 0}
    if n_blocks % 2 or n_blocks > t:
        raise ValueError("n_blocks must be even and no larger than the number of periods")

    # Per-block sufficient statistics let every split's Sharpe be computed without touching raw returns.
    bounds = np.linspace(0, t, n_blocks + 1).astype(int)
    cnt = np.array([bounds[i + 1] - bounds[i] for i in range(n_blocks)], dtype=float)
    s1 = np.array([returns[bounds[i] : bounds[i + 1]].sum(axis=0) for i in range(n_blocks)])
    s2 = np.array([(returns[bounds[i] : bounds[i + 1]] ** 2).sum(axis=0) for i in range(n_blocks)])

    combos = list(itertools.combinations(range(n_blocks), n_blocks // 2))
    if len(combos) > max_splits:
        rng = np.random.default_rng(seed)
        combos = [combos[i] for i in rng.choice(len(combos), size=max_splits, replace=False)]

    def sharpe_of(blocks: np.ndarray) -> np.ndarray:
        k = cnt[blocks].sum()
        mean = s1[blocks].sum(axis=0) / k
        var = s2[blocks].sum(axis=0) / k - mean**2
        return mean / np.sqrt(np.where(var > 0, var, np.nan))

    logits, is_best, oos_best = [], [], []
    all_blocks = np.arange(n_blocks)
    for combo in combos:
        ins = np.array(combo)
        oos = np.setdiff1d(all_blocks, ins)
        sr_in, sr_out = sharpe_of(ins), sharpe_of(oos)
        if not np.isfinite(sr_in).any():
            continue
        best = int(np.nanargmax(sr_in))
        rank = (np.sum(sr_out < sr_out[best]) + 0.5 * np.sum(sr_out == sr_out[best]) + 0.5) / (n + 1)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1 - rank)))
        is_best.append(float(sr_in[best]))
        oos_best.append(float(sr_out[best]))
    lg = np.array(logits)
    return {
        "pbo": float(np.mean(lg <= 0)) if len(lg) else float("nan"),
        "logits": lg.tolist(),
        "is_sharpe": is_best,
        "oos_sharpe": oos_best,
        "n_splits": len(lg),
    }
