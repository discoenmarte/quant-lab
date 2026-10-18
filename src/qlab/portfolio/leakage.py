"""Look-ahead detection for portfolio strategies, by perturbing the future.

Same test as ``backtest.leakage`` for single instruments: if any weight at date t changes when only prices after
t change, the strategy reads the future.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .panel import Panel
from .strategies import PortfolioStrategy


def future_shift_check(
    strategy: PortfolioStrategy,
    panel: Panel,
    params: dict[str, float] | None = None,
    n_cuts: int = 5,
    seed: int = 7,
) -> dict[str, Any]:
    """Check that changing future prices never changes past weights.

    For each of ``n_cuts`` random cut dates, every price after the cut is scaled by independent random factors
    between 0.5 and 1.5 (open and close of a date by the same factor, so bars stay valid), weights are recomputed,
    and weights up to and including the cut must be identical.

    Args:
        strategy: Portfolio strategy to test.
        panel: Price panel.
        params: Parameter overrides.
        n_cuts: Number of cut dates.
        seed: Random seed.

    Returns:
        ``{"passed": bool, "cuts": [index, ...], "first_violation": index or None}``.
    """
    rng = np.random.default_rng(seed)
    base = strategy.weights(panel, params)
    t = len(panel.ts)
    lo = max(t // 10, 1)
    cuts = sorted(rng.integers(lo, t - 1, size=n_cuts).tolist()) if t > lo + 1 else []
    for cut in cuts:
        factor = np.ones((t, panel.n))
        factor[cut + 1:] = rng.uniform(0.5, 1.5, size=(t - cut - 1, panel.n))
        shocked = panel.with_prices(panel.open * factor, panel.close * factor)
        again = strategy.weights(shocked, params)
        diff = np.flatnonzero(~np.isclose(base[: cut + 1], again[: cut + 1]).all(axis=1))
        if diff.size:
            return {"passed": False, "cuts": cuts, "first_violation": int(diff[0])}
    return {"passed": True, "cuts": cuts, "first_violation": None}
