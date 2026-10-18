"""Weights engine for panels, with the same timing and cost rules as the single-asset engine.

Timing (no look-ahead by construction): the strategy decides weights ``W[t]`` at date t's close; they are
traded at each instrument's next open and held from there. Overnight, the book holds yesterday's weights through
the gaps; at the open it is rebalanced to the new weights, which ride the open-to-close moves. So a day's return
compounds the two legs, ``(1 + Σ overnight·gap) · (1 + Σ held·intraday) − 1``, as the single-asset engine does.
Adding the legs instead drops a small cross-term that a near-benchmark strategy turns into a fake edge. Costs are
charged on every change and cash earns the risk-free rate; cash below zero (leverage) pays it plus a spread.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .panel import Panel

FINANCING_SPREAD = 0.01  # a year, over the risk-free rate, on borrowed cash (typical retail margin is higher)


@dataclass
class PortfolioResult:
    """What a portfolio backtest produces, one row per date.

    Attributes:
        net: Net return per day.
        gross: Return from price moves only.
        cash: Interest on cash (negative when borrowing).
        costs: Trading costs (positive, subtracted).
        held: Weights held during each day's session, shape ``(T, N)``.
        benchmark: Net return of equal weight in every instrument, rebalanced daily through the same engine.
        turnover: Sum of absolute weight changes per day.
    """

    net: np.ndarray
    gross: np.ndarray
    cash: np.ndarray
    costs: np.ndarray
    held: np.ndarray
    benchmark: np.ndarray
    turnover: np.ndarray


def run_portfolio(panel: Panel, weights: np.ndarray, financing_spread: float = FINANCING_SPREAD) -> PortfolioResult:
    """Simulate holding target weights decided at each close.

    Args:
        panel: Aligned prices, costs and the risk-free rate.
        weights: Target weights decided at each date's close, shape ``(T, N)``; NaN means 0. Negative is short.
        financing_spread: Yearly spread over the risk-free rate paid on borrowed cash.

    Returns:
        A ``PortfolioResult``. The first two dates carry no position.
    """
    o, c = panel.open, panel.close
    prev = np.vstack([np.full((1, panel.n), np.nan), c[:-1]])
    gap = np.nan_to_num(o / prev - 1.0)
    intra = np.nan_to_num(c / o - 1.0)
    daily_spread = financing_spread / panel.dpy

    def simulate(w: np.ndarray) -> tuple[np.ndarray, ...]:
        held = np.vstack([np.zeros((1, panel.n)), w[:-1]])
        overnight = np.vstack([np.zeros((1, panel.n)), held[:-1]])
        gross = (1.0 + (overnight * gap).sum(axis=1)) * (1.0 + (held * intra).sum(axis=1)) - 1.0
        change = np.abs(held - overnight)
        costs = (change * panel.costs).sum(axis=1)
        free = 1.0 - held.sum(axis=1)
        cash = np.where(free >= 0, free * panel.rf, free * (panel.rf + daily_spread))
        return gross + cash - costs, gross, cash, costs, held, change.sum(axis=1)

    net, gross, cash, costs, held, turnover = simulate(np.nan_to_num(np.asarray(weights, dtype=float)))
    # The benchmark goes through the same fills and costs, so equal weight scores exactly zero edge against it.
    bench = simulate(np.full((len(c), panel.n), 1.0 / panel.n))[0]
    return PortfolioResult(net=net, gross=gross, cash=cash, costs=costs, held=held, benchmark=bench,
                           turnover=turnover)
