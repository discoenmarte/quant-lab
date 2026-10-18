"""Weights engine for panels, with the same timing and cost rules as the single-asset engine.

Timing (no look-ahead by construction): the strategy decides weights ``W[t]`` at date t's close; they are
traded at each instrument's next open and held from there. Overnight, the book holds yesterday's weights through
the gaps; at the open it is rebalanced to the new weights, which ride the open-to-close moves. So a day's return
compounds the two legs, ``(1 + Σ overnight·gap) · (1 + Σ held·intraday) − 1``, as the single-asset engine does.
Adding the legs instead drops a small cross-term that a near-benchmark strategy turns into a fake edge. Costs are
charged on every change and cash earns the risk-free rate; cash below zero (leverage) pays it plus a spread.

Two holding conventions:

- **Static panels (M3b) hold constant fractions:** each day's session starts at the target weights, so price
  moves are rebalanced away for free. At ETF dispersion and costs this changes little, and keeping it keeps the
  M3b calibration (study D) and verdicts valid.
- **Dynamic panels (M3c) hold drifting positions:** the book trades only on days its targets change, and in
  between each position grows with its own price, as a real account does. In a crypto cross-section, free daily
  rebalancing would be a hidden daily-reversal trade worth several percent a year for any rule that tilts
  toward volatile coins, so the drift convention is the honest one there. Targets are checked against
  point-in-time membership on the days the strategy changes them; a position bought as a member is kept until
  the next change even if the coin falls out of the top N (it still trades). A position whose instrument has no
  bar the next day (a delisting or a halt) is sold at its last close and the money waits in cash. That exit is
  optimistic: a real delisting is announced days ahead with prices falling and spreads widening, and an
  unannounced halt can trap the position.
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
        A ``PortfolioResult``. The first date carries no position. The benchmark is the ``equal_weight``
        strategy through the same engine: 1/N rebalanced daily on a static panel, equal weight of the members
        rebalanced weekly on a dynamic one.
    """
    if panel.drift:
        from .strategies import equal_weight  # strategies import this module's panel types only

        w = np.nan_to_num(np.asarray(weights, dtype=float))
        net, gross, cash, costs, held, turnover = _simulate_drift(panel, w, financing_spread)
        bench = _simulate_drift(panel, equal_weight(panel), financing_spread)[0]
        return PortfolioResult(net=net, gross=gross, cash=cash, costs=costs, held=held, benchmark=bench,
                               turnover=turnover)
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


def effective_targets(weights: np.ndarray, member: np.ndarray) -> np.ndarray:
    """Targets with membership enforced on the days the strategy changes them, and carried in between.

    Args:
        weights: Target weights per date, ``(T, N)``, NaN as 0.
        member: Point-in-time membership, ``(T, N)``.

    Returns:
        ``(T, N)``: on a day the weights change, the weights times that day's membership; otherwise the previous
        day's targets.
    """
    w = np.nan_to_num(np.asarray(weights, dtype=float))
    decide = np.ones(len(w), dtype=bool)
    decide[1:] = (w[1:] != w[:-1]).any(axis=1)
    last = np.maximum.accumulate(np.where(decide, np.arange(len(w)), 0))
    return (w * member)[last]


def _simulate_drift(panel: Panel, weights: np.ndarray, financing_spread: float) -> tuple[np.ndarray, ...]:
    """Day-by-day book with drifting positions, trades on target changes and exits at the last close.

    Each day: the book carried from yesterday's close moves with the overnight gaps; at the open it trades to the
    targets if they changed (instruments without a bar today stay in cash), otherwise it keeps the drifted
    positions; the session's moves change the fractions again; and positions in instruments with no bar
    tomorrow are sold at today's close. Returns compound the two legs as in the constant-fraction engine.

    Args:
        panel: A panel with availability and membership.
        weights: Target weights decided at each close, ``(T, N)``, NaN as 0.
        financing_spread: Yearly spread over the risk-free rate on borrowed cash.

    Returns:
        ``(net, gross, cash, costs, held, turnover)`` as in ``PortfolioResult``.
    """
    t_len, n = panel.close.shape
    a = panel.avail
    gap = np.ones((t_len, n))
    with np.errstate(invalid="ignore", divide="ignore"):
        gap[1:] = np.where(a[1:] & a[:-1], panel.open[1:] / panel.close[:-1], 1.0)
        intra = np.where(a, panel.close / panel.open, 1.0)
    leaves = np.zeros((t_len, n), dtype=bool)
    leaves[:-1] = a[:-1] & ~a[1:]
    targets = effective_targets(weights, panel.member)
    trade = np.zeros(t_len, dtype=bool)  # trade at the open of t when the targets decided at t-1 changed
    trade[1] = targets[0].any()
    trade[2:] = (targets[1:-1] != targets[:-2]).any(axis=1)
    cost, rf, spread = panel.costs, panel.rf, financing_spread / panel.dpy

    net, gross, cash, costs, turnover = (np.zeros(t_len) for _ in range(5))
    held = np.zeros((t_len, n))
    carried = np.zeros(n)  # fractions of equity at yesterday's close
    for t in range(t_len):
        night = float(carried @ (gap[t] - 1.0))
        drifted = carried * gap[t] / (1.0 + night) if 1.0 + night > 0 else np.zeros(n)
        h = np.where(a[t], targets[t - 1], 0.0) if trade[t] else drifted
        traded = np.abs(h - drifted)
        day = float(h @ (intra[t] - 1.0))
        gross[t] = (1.0 + night) * (1.0 + day) - 1.0
        free = 1.0 - h.sum()
        cash[t] = free * rf[t] if free >= 0 else free * (rf[t] + spread)
        at_close = h * intra[t] / (1.0 + day) if 1.0 + day > 0 else np.zeros(n)
        sold = np.abs(at_close) * leaves[t]
        costs[t] = float(traded @ cost + sold @ cost)
        turnover[t] = traded.sum() + sold.sum()
        held[t] = h
        carried = np.where(leaves[t], 0.0, at_close)
    net = gross + cash - costs
    return net, gross, cash, costs, held, turnover
