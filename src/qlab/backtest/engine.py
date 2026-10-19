"""Vectorised backtest engine with next-open execution and explicit costs.

Timing (no look-ahead by construction):

- The strategy decides ``target[t]`` at bar t's close.
- The trade fills at bar t+1's open. During bar t+1, from open to close, the position is ``target[t]``.
- Overnight, from close t to open t+1, the position is the one filled at open t: ``target[t-1]``.

So bar t's return is ``(1 + target[t-2] * gap[t]) * (1 + target[t-1] * intraday[t]) - 1``, minus the cost of
changing the position at bar t's open, plus the risk-free rate on uninvested money.

Session strategies (M5) hold only one leg of each day:

- ``overnight``: the target decided at close t-2 is bought at close t-1 and sold at open t, so bar t earns
  ``target[t-2] * gap[t]``. Buying at a close needs the order in before the close is known, which is why the
  decision is a day older than the close it trades at.
- ``day``: the target decided at close t-1 is bought at open t and sold at close t, earning
  ``target[t-1] * intraday[t]``.

Either way every day with a position is a round trip, so it pays the cost twice.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..data.bars import adjusted


@dataclass(frozen=True)
class CostModel:
    """Trading costs, in basis points of traded notional, charged per side.

    Attributes:
        commission_bps: Broker or exchange fee.
        spread_bps: Full bid-ask spread; half is paid on each trade.
        slippage_bps: Price impact and timing slippage beyond the spread.
    """

    commission_bps: float = 0.0
    spread_bps: float = 2.0
    slippage_bps: float = 1.0

    @property
    def per_unit(self) -> float:
        """Cost of trading one unit of position (100% of equity), as a fraction."""
        return (self.commission_bps + self.spread_bps / 2 + self.slippage_bps) / 1e4


COST_PRESETS: dict[str, CostModel] = {
    "etf": CostModel(commission_bps=0.0, spread_bps=2.0, slippage_bps=1.0),
    "crypto": CostModel(commission_bps=10.0, spread_bps=2.0, slippage_bps=3.0),
    # Altcoins beyond the majors: the same taker fee, a 10 bps spread and more slippage (20 bps a side in all).
    "crypto_xs": CostModel(commission_bps=10.0, spread_bps=10.0, slippage_bps=5.0),
    "zero": CostModel(0.0, 0.0, 0.0),
}
SESSIONS = ("all", "overnight", "day")


@dataclass
class BacktestResult:
    """Everything a backtest produces, aligned on the bar timestamps.

    Attributes:
        ts: Bar timestamps (UTC).
        returns: Net return per bar.
        gross: Return before costs and cash interest.
        cash: Interest earned on uninvested money per bar.
        costs: Trading cost per bar (positive number, subtracted from returns).
        position: Position held during each bar's session (filled at its open).
        benchmark: Buy-and-hold return per bar on the same prices.
        trades: One row per round trip: entry/exit time and raw price, direction, bars held, net return.
    """

    ts: pd.Series
    returns: pd.Series
    gross: pd.Series
    cash: pd.Series
    costs: pd.Series
    position: pd.Series
    benchmark: pd.Series
    trades: pd.DataFrame


def run(
    bars: pd.DataFrame,
    target: pd.Series,
    costs: CostModel,
    rf: pd.Series | None = None,
    use_adjusted: bool = True,
    with_trades: bool = True,
    session: str = "all",
) -> BacktestResult:
    """Simulate holding a target position series.

    Args:
        bars: Normalised bars (``ts, open, high, low, close`` and optionally ``adj_close``), positional index.
        target: Target position decided at each bar's close, aligned with ``bars``; values in [-1, 1].
        costs: Cost model applied to every position change.
        rf: Risk-free return per bar, aligned with ``bars``. None means zero.
        use_adjusted: Compute returns on dividend-adjusted prices when available.
        with_trades: Build the round-trip table. The gauntlet runs thousands of backtests that only need
            returns, and the table is most of the run time for strategies that trade often.
        session: ``all`` (hold through day and night), ``overnight`` (close to next open only) or ``day`` (open
            to close only); see the module docstring.

    Returns:
        A ``BacktestResult``. The first two bars carry no position (nothing could have been traded yet).
        ``trades`` is empty when ``with_trades`` is False. For a session strategy ``position`` is the leg held.

    Raises:
        ValueError: Unknown session.
    """
    if session not in SESSIONS:
        raise ValueError(f"session must be one of {', '.join(SESSIONS)}")
    px = adjusted(bars) if use_adjusted else bars
    o = px["open"].to_numpy(float)
    c = px["close"].to_numpy(float)
    prev_close = np.roll(c, 1)
    prev_close[0] = np.nan
    gap = np.nan_to_num(o / prev_close - 1.0)
    intraday = np.nan_to_num(c / o - 1.0)

    tgt = target.reindex(bars.index).fillna(0.0).clip(-1.0, 1.0).to_numpy(float)
    held = np.concatenate([[0.0], tgt[:-1]])  # filled at this bar's open
    overnight = np.concatenate([[0.0], held[:-1]])  # held from the previous close to this open

    if session != "all":
        # One leg per day, each a round trip: the other leg is flat.
        leg = overnight if session == "overnight" else held
        overnight, held = (leg, np.zeros_like(leg)) if session == "overnight" else (np.zeros_like(leg), leg)
    gross = (1.0 + overnight * gap) * (1.0 + held * intraday) - 1.0
    rf_arr = np.zeros(len(c)) if rf is None else rf.reindex(bars.index).fillna(0.0).to_numpy(float)
    if session == "all":
        cost = np.abs(held - overnight) * costs.per_unit
        cash = (1.0 - np.abs(held)) * rf_arr
    else:
        leg = overnight + held
        cost = 2.0 * np.abs(leg) * costs.per_unit
        cash = (1.0 - np.abs(leg)) * rf_arr
        held = leg
    net = gross + cash - cost

    bench = np.nan_to_num(c / prev_close - 1.0)
    idx = bars.index
    return BacktestResult(
        ts=bars["ts"],
        returns=pd.Series(net, index=idx),
        gross=pd.Series(gross, index=idx),
        cash=pd.Series(cash, index=idx),
        costs=pd.Series(cost, index=idx),
        position=pd.Series(held, index=idx),
        benchmark=pd.Series(bench, index=idx),
        trades=_trades(bars, held, net) if with_trades else _trades(bars.iloc[:0], held[:0], net[:0]),
    )


def _trades(bars: pd.DataFrame, held: np.ndarray, net: np.ndarray) -> pd.DataFrame:
    """Split the held-position series into round trips.

    A trade starts when the position's sign changes away from zero and ends when it changes again. Prices
    are raw opens, which is what an order would have seen.

    Args:
        bars: Normalised bars.
        held: Position held during each bar.
        net: Net return per bar.

    Returns:
        Columns ``entry_ts, exit_ts, entry_price, exit_price, side, bars, ret``; an open trade has no exit.
    """
    sign = np.sign(held)
    rows = []
    start = None
    for i in range(len(sign)):
        changed = i == 0 or sign[i] != sign[i - 1]
        if changed and start is not None:
            rows.append(_trade_row(bars, start, i, sign[start], net))
            start = None
        if changed and sign[i] != 0:
            start = i
    if start is not None:
        rows.append(_trade_row(bars, start, None, sign[start], net))
    return pd.DataFrame(rows, columns=["entry_ts", "exit_ts", "entry_price", "exit_price", "side", "bars", "ret"])


def _trade_row(bars: pd.DataFrame, start: int, end: int | None, side: float, net: np.ndarray) -> dict:
    """One round trip as a record.

    Args:
        bars: Normalised bars.
        start: Index of the bar whose open filled the entry.
        end: Index of the bar whose open filled the exit; None while still open.
        side: 1.0 long, -1.0 short.
        net: Net return per bar.

    Returns:
        A trade dict (see ``_trades``).
    """
    stop = end if end is not None else len(bars)
    return {
        "entry_ts": bars["ts"].iloc[start],
        "exit_ts": bars["ts"].iloc[end] if end is not None else None,
        "entry_price": float(bars["open"].iloc[start]),
        "exit_price": float(bars["open"].iloc[end]) if end is not None else None,
        "side": "long" if side > 0 else "short",
        "bars": int(stop - start),
        "ret": float(np.prod(1.0 + net[start:stop]) - 1.0),
    }
