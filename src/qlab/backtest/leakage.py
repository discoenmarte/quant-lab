"""Look-ahead detection by perturbing the future.

If a strategy's position at bar t changes when only data *after* t changes, the strategy is reading the
future. This test is used on every registered strategy (tests) and as gauntlet stage 1.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .strategies import Strategy

PRICE_COLS = ["open", "high", "low", "close", "adj_close"]


def future_shift_check(
    strategy: Strategy,
    bars: pd.DataFrame,
    params: dict[str, float] | None = None,
    n_cuts: int = 5,
    seed: int = 7,
) -> dict[str, Any]:
    """Check that changing future bars never changes past positions.

    For each of ``n_cuts`` random cut points, every bar after the cut is scaled by random factors between
    0.5 and 1.5 (consistently across OHLC so bars stay valid), positions are recomputed, and positions up to
    and including the cut must be identical.

    Args:
        strategy: Registered strategy to test.
        bars: Normalised bars with a positional index.
        params: Parameter overrides.
        n_cuts: Number of cut points.
        seed: Random seed, for reproducibility.

    Returns:
        ``{"passed": bool, "cuts": [index, ...], "first_violation": index or None}``.
    """
    rng = np.random.default_rng(seed)
    base = strategy.positions(bars, params).to_numpy()
    lo = max(len(bars) // 10, 1)
    cuts = sorted(rng.integers(lo, len(bars) - 1, size=n_cuts).tolist()) if len(bars) > lo + 1 else []
    for cut in cuts:
        shocked = bars.copy()
        factor = rng.uniform(0.5, 1.5, size=len(bars) - cut - 1)
        for col in PRICE_COLS:
            if col in shocked:
                shocked.loc[shocked.index[cut + 1 :], col] = shocked[col].iloc[cut + 1 :].to_numpy() * factor
        again = strategy.positions(shocked, params).to_numpy()
        diff = np.flatnonzero(~np.isclose(base[: cut + 1], again[: cut + 1]))
        if diff.size:
            return {"passed": False, "cuts": cuts, "first_violation": int(diff[0])}
    return {"passed": True, "cuts": cuts, "first_violation": None}
