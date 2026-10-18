"""Portfolio strategies: the diversified, published rules practitioners actually run.

Contract: a strategy is a pure function ``f(panel, **params) -> weights`` returning a ``(T, N)`` array of target
weights decided at each date's close, using only prices up to that close. The engine trades them at the next
open. Weights are fractions of equity; their absolute sum is gross exposure.

Every rule rebalances on a fixed schedule (every ``rebalance`` trading days from the start of the panel) and
holds its weights in between, which keeps turnover and costs realistic. Volatility targeting uses an ex-ante
estimate: today's weights applied to the past ``vol_lookback`` days of returns.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from ..backtest.strategies import Param
from .panel import Panel

MAX_GROSS = 2.0  # cap on gross exposure after volatility scaling: leverage beyond 2x is not a retail setup


@dataclass(frozen=True)
class PortfolioStrategy:
    """A registered portfolio strategy.

    Attributes:
        name: Registry key.
        family: Research family; trials on one family and universe form one research question.
        description: One-paragraph explanation for the UI.
        fn: The weights function.
        params: Tunable parameters by name.
        reference: Paper the rule comes from.
    """

    name: str
    family: str
    description: str
    fn: Callable[..., np.ndarray]
    params: dict[str, Param] = field(default_factory=dict)
    reference: str = ""

    def defaults(self) -> dict[str, float]:
        """Default parameter values, with integer parameters as int."""
        return {k: (int(p.default) if p.is_int else p.default) for k, p in self.params.items()}

    def weights(self, panel: Panel, params: dict[str, float] | None = None) -> np.ndarray:
        """Target weights with defaults filled in and NaN (warm-up) as 0.

        Args:
            panel: Price panel.
            params: Parameter overrides.

        Returns:
            Array of shape ``(T, N)``.
        """
        merged = {**self.defaults(), **(params or {})}
        merged = {k: (int(v) if self.params[k].is_int else float(v)) for k, v in merged.items()}
        return np.nan_to_num(np.asarray(self.fn(panel, **merged), dtype=float))


def _closes(panel: Panel) -> pd.DataFrame:
    """Closes as a DataFrame on a positional index, for rolling windows.

    Args:
        panel: Price panel.

    Returns:
        DataFrame ``(T, N)``.
    """
    return pd.DataFrame(panel.close)


def _hold(target: np.ndarray, every: int) -> np.ndarray:
    """Keep each rebalance day's weights until the next rebalance.

    Args:
        target: Weights computed every day, ``(T, N)``; NaN during warm-up.
        every: Rebalance every this many rows.

    Returns:
        Weights that change only on rows ``0, every, 2·every, ...``; 0 before the first usable rebalance.
    """
    out = pd.DataFrame(np.where(np.arange(len(target))[:, None] % max(every, 1) == 0, target, np.nan))
    return out.ffill().fillna(0.0).to_numpy()


def _ex_ante_vol(w: np.ndarray, r: np.ndarray, lookback: int, dpy: int) -> np.ndarray:
    """Annual volatility of today's weights applied to the last ``lookback`` days of returns.

    Args:
        w: Weights, ``(T, N)``.
        r: Close-to-close returns, ``(T, N)``.
        lookback: Window in days.
        dpy: Days per year.

    Returns:
        ``(T,)`` volatility, NaN before a full window.
    """
    out = np.full(len(r), np.nan)
    if len(r) < lookback:
        return out
    windows = sliding_window_view(r, (lookback, r.shape[1]))[:, 0]  # (T - L + 1, L, N), window ends at row t
    port = np.einsum("tln,tn->tl", windows, w[lookback - 1:])
    out[lookback - 1:] = port.std(axis=1, ddof=1) * math.sqrt(dpy)
    return out


def _scale(w: np.ndarray, r: np.ndarray, target_vol: float, lookback: int, dpy: int) -> np.ndarray:
    """Scale weights to a target annual volatility, capping gross exposure at ``MAX_GROSS``.

    Args:
        w: Unscaled weights, ``(T, N)``.
        r: Close-to-close returns, ``(T, N)``.
        target_vol: Annual volatility to aim for.
        lookback: Window for the volatility estimate.
        dpy: Days per year.

    Returns:
        Scaled weights; NaN where no estimate exists yet.
    """
    vol = _ex_ante_vol(np.nan_to_num(w), r, lookback, dpy)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(vol > 0, target_vol / vol, np.nan)
    gross = np.abs(np.nan_to_num(w)).sum(axis=1)
    with np.errstate(divide="ignore"):
        k = np.minimum(k, np.where(gross > 0, MAX_GROSS / gross, np.nan))
    return w * k[:, None]


def _past_return(panel: Panel, lookback: int, skip: int) -> np.ndarray:
    """Return from ``lookback`` days ago to ``skip`` days ago, per instrument.

    Args:
        panel: Price panel.
        lookback: Start of the window, days back.
        skip: End of the window, days back (skipping the most recent month avoids short-term reversal).

    Returns:
        ``(T, N)``; NaN during warm-up.
    """
    c = _closes(panel)
    return (c.shift(skip) / c.shift(lookback) - 1.0).to_numpy()


def equal_weight(panel: Panel) -> np.ndarray:
    """1/N in every instrument, rebalanced daily. The benchmark that is hard to beat (DeMiguel et al., 2009).

    Args:
        panel: Price panel.

    Returns:
        Weights ``(T, N)``.
    """
    return np.full((len(panel.ts), panel.n), 1.0 / panel.n)


def risk_parity(panel: Panel, lookback: int = 63, target_vol: float = 0.10, rebalance: int = 21) -> np.ndarray:
    """Inverse-volatility weights scaled to a volatility target: every instrument contributes similar risk.

    Args:
        panel: Price panel.
        lookback: Volatility window, days.
        target_vol: Annual portfolio volatility to aim for.
        rebalance: Rebalance every this many days.

    Returns:
        Long-only weights ``(T, N)``.
    """
    r = panel.returns()
    vol = pd.DataFrame(r).rolling(lookback, min_periods=lookback).std().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        inv = np.where(vol > 0, 1.0 / vol, np.nan)
    raw = inv / np.nansum(inv, axis=1, keepdims=True)
    return _hold(_scale(raw, r, target_vol, lookback, panel.dpy), rebalance)


def tsmom(
    panel: Panel,
    lookback: int = 252,
    skip: int = 21,
    target_vol: float = 0.10,
    allow_short: int = 1,
    rebalance: int = 21,
) -> np.ndarray:
    """Time-series momentum across instruments: each one long or short by the sign of its own past year, sized by
    inverse volatility, the whole book scaled to a volatility target (Moskowitz, Ooi & Pedersen, 2012).

    Args:
        panel: Price panel.
        lookback: Return window, days.
        skip: Most recent days left out of the window.
        target_vol: Annual portfolio volatility to aim for.
        allow_short: 1 to short instruments with negative momentum, 0 to hold cash instead.
        rebalance: Rebalance every this many days.

    Returns:
        Weights ``(T, N)``.
    """
    r = panel.returns()
    past = _past_return(panel, lookback, skip)
    sign = np.where(past > 0, 1.0, np.where(past < 0, -1.0 if allow_short else 0.0, np.nan))
    vol = pd.DataFrame(r).rolling(63, min_periods=63).std().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = sign / vol
        raw = raw / np.nansum(np.abs(raw), axis=1, keepdims=True)
    return _hold(_scale(raw, r, target_vol, 63, panel.dpy), rebalance)


def trend_filter(panel: Panel, window: int = 210, rebalance: int = 21) -> np.ndarray:
    """Equal weight in every instrument above its moving average, cash for the rest (Faber, 2007).

    Args:
        panel: Price panel.
        window: Moving-average length, days (210 is about ten months).
        rebalance: Rebalance every this many days.

    Returns:
        Long-only weights ``(T, N)`` summing to at most 1.
    """
    c = _closes(panel)
    sma = c.rolling(window, min_periods=window).mean()
    above = np.where(sma.isna().to_numpy(), np.nan, (c > sma).to_numpy(float))
    return _hold(above / panel.n, rebalance)


def xs_momentum(
    panel: Panel, lookback: int = 252, skip: int = 21, top: int = 3, abs_filter: int = 1, rebalance: int = 21
) -> np.ndarray:
    """Hold the instruments with the strongest past year, equally weighted (Asness, Moskowitz & Pedersen, 2013);
    with the absolute filter, only those whose own momentum is positive, the rest in cash (Antonacci's dual
    momentum).

    Args:
        panel: Price panel.
        lookback: Return window, days.
        skip: Most recent days left out of the window.
        top: Number of instruments held.
        abs_filter: 1 to require positive momentum, 0 to always hold ``top`` instruments.
        rebalance: Rebalance every this many days.

    Returns:
        Long-only weights ``(T, N)``.
    """
    past = _past_return(panel, lookback, skip)
    k = min(top, panel.n)
    ranks = pd.DataFrame(past).rank(axis=1, ascending=False, method="first").to_numpy()
    chosen = ranks <= k
    if abs_filter:
        chosen &= past > 0
    w = np.where(chosen, 1.0 / k, 0.0)
    w[np.isnan(past).any(axis=1)] = np.nan
    return _hold(w, rebalance)


def vol_managed(panel: Panel, vol_lookback: int = 21, target_vol: float = 0.10, rebalance: int = 5) -> np.ndarray:
    """Equal weight scaled up in calm markets and down in turbulent ones (Moreira & Muir, 2017).

    Args:
        panel: Price panel.
        vol_lookback: Volatility window, days.
        target_vol: Annual portfolio volatility to aim for.
        rebalance: Rebalance every this many days.

    Returns:
        Long-only weights ``(T, N)``.
    """
    r = panel.returns()
    return _hold(_scale(equal_weight(panel), r, target_vol, vol_lookback, panel.dpy), rebalance)


VOL = Param(0.10, 0.05, 0.20, 0.05, "Annual volatility target")

REGISTRY: dict[str, PortfolioStrategy] = {
    s.name: s
    for s in [
        PortfolioStrategy("equal_weight", "equal_weight", "1/N in every instrument. The benchmark.", equal_weight,
                          reference="DeMiguel, Garlappi & Uppal (2009)"),
        PortfolioStrategy(
            "risk_parity", "risk_parity",
            "Inverse-volatility weights, scaled to a volatility target, so each asset class carries similar risk.",
            risk_parity,
            {"lookback": Param(63, 21, 252, 21, "Volatility window, days"), "target_vol": VOL,
             "rebalance": Param(21, 7, 63, 7, "Days between rebalances")},
            "Qian (2005); Asness, Frazzini & Pedersen (2012)",
        ),
        PortfolioStrategy(
            "tsmom", "tsmom_portfolio",
            "Long or short each instrument by the sign of its past year, sized by inverse volatility, the book "
            "scaled to a volatility target. The classic managed-futures trend portfolio.",
            tsmom,
            {"lookback": Param(252, 63, 378, 21, "Return window, days"),
             "skip": Param(21, 0, 42, 7, "Recent days skipped"),
             "target_vol": VOL,
             "allow_short": Param(1, 0, 1, 1, "1 = short losers, 0 = cash"),
             "rebalance": Param(21, 7, 63, 7, "Days between rebalances")},
            "Moskowitz, Ooi & Pedersen (2012); Hurst, Ooi & Pedersen (2017)",
        ),
        PortfolioStrategy(
            "trend_filter", "trend_filter",
            "Equal weight in each instrument trading above its ten-month average, cash for the rest.",
            trend_filter,
            {"window": Param(210, 60, 300, 10, "Moving average, days"),
             "rebalance": Param(21, 7, 63, 7, "Days between rebalances")},
            "Faber (2007), A Quantitative Approach to Tactical Asset Allocation",
        ),
        PortfolioStrategy(
            "xs_momentum", "xs_momentum",
            "Hold the strongest instruments of the past year; optionally only those whose own trend is up.",
            xs_momentum,
            {"lookback": Param(252, 63, 378, 21, "Return window, days"),
             "skip": Param(21, 0, 42, 21, "Recent days skipped"),
             "top": Param(3, 1, 6, 1, "Instruments held"),
             "abs_filter": Param(1, 0, 1, 1, "1 = only positive momentum"),
             "rebalance": Param(21, 7, 63, 7, "Days between rebalances")},
            "Asness, Moskowitz & Pedersen (2013); Antonacci (2014)",
        ),
        PortfolioStrategy(
            "vol_managed", "vol_managed",
            "Equal weight, scaled up when recent volatility is low and down when it is high.",
            vol_managed,
            {"vol_lookback": Param(21, 7, 126, 7, "Volatility window, days"), "target_vol": VOL,
             "rebalance": Param(5, 1, 21, 2, "Days between rebalances")},
            "Moreira & Muir (2017); Harvey et al. (2018)",
        ),
    ]
}
