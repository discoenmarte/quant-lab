"""The intraday library: rules that hold a position for part of one UTC day and are flat at its end.

Design (decision 0015):

- **The crypto day.** Crypto trades around the clock; the lab's day is the UTC date, and its "open" is 00:00 UTC,
  when Binance's daily candle starts. Bars are hourly; a position decided at a bar's close is held from the next
  bar's open, as everywhere in the lab.
- **Flat at the end of the day.** Every rule leaves the market at 00:00 UTC (its position on the day's last bar is
  zero), so it pays a round trip on every day it trades: about 28 basis points with the crypto cost model.
- **The selector.** The roadmap's morning selector, kept honest: it computes, it doesn't search. ``vol_filter``
  trades only after a day whose realised volatility was above its median of the previous 60 days (Gao, Han, Li and
  Zhou, 2018, found intraday momentum strongest on volatile days). It is a parameter of each rule, so the gauntlet
  judges the rule and its selector together.
- Rules read only bars up to the decision; the gauntlet's look-ahead check runs on them like on any other rule.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOURS = 24
VOL_DAYS = 60


def _calendar(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """UTC day number and hour of each bar.

    Args:
        bars: Normalised hourly bars.

    Returns:
        ``(day, hour)`` as integer arrays.

    Raises:
        ValueError: The bars aren't hourly (a daily series would never trade).
    """
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts"], utc=True)).as_unit("ns")  # stored series may be in microseconds
    ns = ts.asi8
    if len(ts) > 1 and float(np.median(np.diff(ns))) > 3.6e12 * 1.5:
        raise ValueError("intraday rules need hourly bars")
    day = ns // (86_400 * 10**9)
    return day.astype(np.int64), ts.hour.to_numpy()


def _volatile_after(day: np.ndarray, close: np.ndarray) -> pd.Series:
    """Whether the day before each day was more volatile than usual.

    Realised volatility is the standard deviation of a day's hourly log returns; a day qualifies when the previous
    day's value is above the median of the previous ``VOL_DAYS`` days' values. Only finished days are used.

    Args:
        day: Day number per bar.
        close: Close per bar.

    Returns:
        Booleans indexed by day number (False until enough history exists).
    """
    r = np.diff(np.log(close), prepend=np.nan)
    rv = pd.Series(r).groupby(day).std()
    prev = rv.shift(1)
    med = prev.shift(1).rolling(VOL_DAYS, min_periods=VOL_DAYS // 3).median()
    return (prev > med).fillna(False)


def _first_window_return(day: np.ndarray, hour: np.ndarray, open_: np.ndarray, close: np.ndarray,
                         window: int) -> pd.Series:
    """Each day's return from 00:00 UTC to the close of its first ``window`` hours.

    Args:
        day: Day number per bar.
        hour: Hour per bar.
        open_: Open per bar.
        close: Close per bar.
        window: Hours.

    Returns:
        Returns indexed by day number; days missing either bar are left out.
    """
    first = pd.Series(open_[hour == 0], index=day[hour == 0])
    last = pd.Series(close[hour == window - 1], index=day[hour == window - 1])
    first = first[~first.index.duplicated()]
    last = last[~last.index.duplicated()]
    return (last / first.reindex(last.index) - 1.0).dropna()


def intraday_momentum(bars: pd.DataFrame, window: int = 1, hold: int = 1, vol_filter: int = 0) -> pd.Series:
    """Hold the day's last hours when its first hours went up; flat otherwise and at the day's end.

    The crypto version of market intraday momentum (Gao, Han, Li and Zhou, 2018: the first half-hour's return
    predicts the last half-hour's), as Wen, Bouri, Xu and Zhao (2022) tested it on Bitcoin.

    Args:
        bars: Normalised hourly bars.
        window: Hours from 00:00 UTC whose return gives the signal (at most ``24 - hold``).
        hold: Last hours of the day to hold (1 holds 23:00-24:00 UTC).
        vol_filter: 1 trades only after a day more volatile than the previous 60 days' median.

    Returns:
        1.0 on the bars whose next hour is held, 0.0 elsewhere.
    """
    window, hold = int(window), int(hold)
    if window < 1 or hold < 1 or window + hold > HOURS:
        return pd.Series(0.0, index=bars.index)
    day, hour = _calendar(bars)
    o, c = bars["open"].to_numpy(float), bars["close"].to_numpy(float)
    up = _first_window_return(day, hour, o, c, window) > 0
    if int(vol_filter):
        up = up & _volatile_after(day, c).reindex(up.index).fillna(False)
    days = up.index[up.to_numpy()]
    # The bar decided at hour h is held from hour h + 1, so hours 24 - hold .. 23 are held from bars 23 - hold .. 22.
    on = np.isin(day, days) & (hour >= HOURS - hold - 1) & (hour <= HOURS - 2) & (hour >= window - 1)
    return pd.Series(on.astype(float), index=bars.index)


def range_breakout(bars: pd.DataFrame, window: int = 4, vol_filter: int = 0) -> pd.Series:
    """Buy when the price closes above the high of the day's first hours; hold to the day's end.

    The crypto version of the opening-range breakout: the range is set from 00:00 UTC; a close above its high after
    it is set enters, and the position is closed at 00:00 UTC the next day. At most one trade a day, long only.

    Args:
        bars: Normalised hourly bars.
        window: Hours that set the range.
        vol_filter: 1 trades only after a day more volatile than the previous 60 days' median.

    Returns:
        1.0 from the breakout bar through the day's second-to-last bar, 0.0 elsewhere.
    """
    window = int(window)
    if window < 1 or window > HOURS - 2:
        return pd.Series(0.0, index=bars.index)
    day, hour = _calendar(bars)
    h, c = bars["high"].to_numpy(float), bars["close"].to_numpy(float)
    in_range = hour < window
    top = pd.Series(h[in_range]).groupby(day[in_range]).max()
    # A range with a missing hour is unusable: its high may be the one that is missing.
    full = pd.Series(hour[in_range]).groupby(day[in_range]).nunique() == window
    top = top[full.reindex(top.index).fillna(False).to_numpy()]
    level = top.reindex(day).to_numpy()
    after = (hour >= window) & (hour <= HOURS - 2)
    with np.errstate(invalid="ignore"):
        brk = after & (c > level)  # NaN levels (no usable range) compare False
    if int(vol_filter):
        ok = _volatile_after(day, c).reindex(day).fillna(False).to_numpy()
        brk = brk & ok
    held = pd.Series(brk.astype(np.int8)).groupby(day).cummax().to_numpy().astype(bool) & after
    return pd.Series(held.astype(float), index=bars.index)
