"""Strategy library.

Contract: a strategy is a pure function ``f(bars, **params) -> pd.Series`` that returns the target position
for each bar, between -1 (fully short) and 1 (fully long). The value at bar t may use only data up to and
including bar t's close. The engine trades it at the next bar's open, so a strategy can never act on the
price it just saw.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Param:
    """One tunable parameter.

    Attributes:
        default: Value used when none is given.
        low: Smallest sensible value (used by sweeps and the plateau test).
        high: Largest sensible value.
        step: Grid step for sweeps.
        doc: What the parameter means, with units.
    """

    default: float
    low: float
    high: float
    step: float
    doc: str

    @property
    def is_int(self) -> bool:
        """True when every bound is a whole number, so the parameter is a count of bars."""
        return all(float(v).is_integer() for v in (self.default, self.low, self.high, self.step))


@dataclass(frozen=True)
class Strategy:
    """A registered strategy.

    Attributes:
        name: Registry key.
        family: Research family; trials on one family and instrument form one research question.
        description: One-paragraph explanation for the UI.
        fn: The position function.
        params: Tunable parameters by name.
        reference: Paper or source the rule comes from.
        starts: First date the rule can be judged from (empty: from the first bar). The gauntlet leaves out earlier
            bars, so years without its inputs don't count as years in cash.
    """

    name: str
    family: str
    description: str
    fn: Callable[..., pd.Series]
    params: dict[str, Param] = field(default_factory=dict)
    reference: str = ""
    starts: str = ""  # first date it can be judged from, when its inputs begin later than prices (YYYY-MM-DD)

    def defaults(self) -> dict[str, float]:
        """Default parameter values.

        Returns:
            Mapping of parameter name to default, with integer parameters as int.
        """
        return {k: (int(p.default) if p.is_int else p.default) for k, p in self.params.items()}

    def positions(self, bars: pd.DataFrame, params: dict[str, float] | None = None) -> pd.Series:
        """Target positions for the bars, with defaults filled in and values clipped to [-1, 1].

        Args:
            bars: Normalised bars.
            params: Parameter overrides.

        Returns:
            Series aligned with ``bars``; NaN (warm-up) becomes 0.
        """
        merged = {**self.defaults(), **(params or {})}
        merged = {k: (int(v) if self.params[k].is_int else float(v)) for k, v in merged.items()}
        return self.fn(bars, **merged).reindex(bars.index).fillna(0.0).clip(-1.0, 1.0)


def _hysteresis(enter: pd.Series, leave: pd.Series) -> pd.Series:
    """Long/flat state machine: go long when ``enter`` is true, go flat when ``leave`` is true.

    Args:
        enter: Boolean entry condition per bar.
        leave: Boolean exit condition per bar (checked while long).

    Returns:
        1.0 while long, 0.0 while flat.
    """
    state = np.zeros(len(enter))
    long = False
    for i, (e, x) in enumerate(zip(enter.to_numpy(), leave.to_numpy(), strict=True)):
        if long and x:
            long = False
        elif not long and e:
            long = True
        state[i] = 1.0 if long else 0.0
    return pd.Series(state, index=enter.index)


def buy_and_hold(bars: pd.DataFrame) -> pd.Series:
    """Always fully invested. The benchmark every other strategy must beat.

    Args:
        bars: Normalised bars.

    Returns:
        1.0 for every bar.
    """
    return pd.Series(1.0, index=bars.index)


def trend_sma(bars: pd.DataFrame, window: int = 200, entry_band: float = 0.01, exit_band: float = 0.03) -> pd.Series:
    """Long above the moving average, flat below it, with bands to avoid whipsaws.

    Args:
        bars: Normalised bars.
        window: Moving-average length in bars.
        entry_band: Go long when the close is this fraction above the average (0.01 = 1%).
        exit_band: Go flat when the close is this fraction below the average.

    Returns:
        1.0 while long, 0.0 while flat; flat during warm-up.
    """
    sma = bars["close"].rolling(window, min_periods=window).mean()
    enter = bars["close"] >= sma * (1 + entry_band)
    leave = bars["close"] <= sma * (1 - exit_band)
    return _hysteresis(enter.fillna(False), leave.fillna(False))


def sma_cross(bars: pd.DataFrame, fast: int = 50, slow: int = 200) -> pd.Series:
    """Long while the fast moving average is above the slow one.

    Args:
        bars: Normalised bars.
        fast: Fast average length in bars.
        slow: Slow average length in bars.

    Returns:
        1.0 while fast > slow, else 0.0.
    """
    f = bars["close"].rolling(fast, min_periods=fast).mean()
    s = bars["close"].rolling(slow, min_periods=slow).mean()
    return (f > s).astype(float)


def tsmom(bars: pd.DataFrame, lookback: int = 252, skip: int = 21, allow_short: int = 0) -> pd.Series:
    """Time-series momentum: hold the direction of the past year's return, skipping the latest month.

    Args:
        bars: Normalised bars.
        lookback: Return window in bars (252 = one stock year).
        skip: Most recent bars excluded from the window (short-term reversal).
        allow_short: 1 to go short after negative returns, 0 to stay flat.

    Returns:
        1.0 after positive past returns; -1.0 or 0.0 after negative ones.
    """
    past = bars["close"].shift(skip) / bars["close"].shift(lookback) - 1.0
    short = -1.0 if allow_short else 0.0
    return pd.Series(np.where(past > 0, 1.0, np.where(past < 0, short, 0.0)), index=bars.index)


def donchian(bars: pd.DataFrame, entry: int = 55, exit: int = 20) -> pd.Series:
    """Breakout: long after a new N-bar high, flat after a new M-bar low.

    Args:
        bars: Normalised bars.
        entry: Breakout window in bars.
        exit: Exit window in bars.

    Returns:
        1.0 while long, 0.0 while flat.
    """
    upper = bars["high"].rolling(entry, min_periods=entry).max().shift(1)
    lower = bars["low"].rolling(exit, min_periods=exit).min().shift(1)
    return _hysteresis((bars["close"] > upper).fillna(False), (bars["close"] < lower).fillna(False))


def rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's relative strength index.

    Args:
        close: Closing prices.
        period: Smoothing length in bars.

    Returns:
        RSI between 0 and 100; NaN during warm-up.
    """
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).where(loss > 0, 100.0)


def rsi_reversion(bars: pd.DataFrame, period: int = 2, buy_below: float = 10, sell_above: float = 70) -> pd.Series:
    """Short-term mean reversion: buy deep oversold readings, sell once the bounce is done.

    Args:
        bars: Normalised bars.
        period: RSI length in bars.
        buy_below: Go long when RSI falls below this level.
        sell_above: Go flat when RSI rises above this level.

    Returns:
        1.0 while long, 0.0 while flat.
    """
    r = rsi(bars["close"], period)
    return _hysteresis((r < buy_below).fillna(False), (r > sell_above).fillna(False))


def _insider_timing(bars: pd.DataFrame, window: int = 60, lookback: int = 730, threshold: float = 0.0) -> pd.Series:
    """Aggregate insider timing (``qlab.informed.insiders.insider_timing``), imported when first used.

    Args:
        bars: Normalised daily bars.
        window: Days of filings per reading.
        lookback: Days of history to standardise against.
        threshold: Hold above this many standard deviations.

    Returns:
        Target positions.
    """
    from ..informed.insiders import insider_timing

    return insider_timing(bars, window, lookback, threshold)


def _intraday_momentum(bars: pd.DataFrame, window: int = 1, hold: int = 1, vol_filter: int = 0) -> pd.Series:
    """Intraday momentum on hourly bars (``qlab.intraday.rules.intraday_momentum``), imported when first used.

    Args:
        bars: Normalised hourly bars.
        window: Hours from 00:00 UTC that give the signal.
        hold: Last hours of the day to hold.
        vol_filter: 1 trades only after a volatile day.

    Returns:
        Target positions.
    """
    from ..intraday.rules import intraday_momentum

    return intraday_momentum(bars, window, hold, vol_filter)


def _range_breakout(bars: pd.DataFrame, window: int = 4, vol_filter: int = 0) -> pd.Series:
    """Opening-range breakout on hourly bars (``qlab.intraday.rules.range_breakout``), imported when first used.

    Args:
        bars: Normalised hourly bars.
        window: Hours that set the range.
        vol_filter: 1 trades only after a volatile day.

    Returns:
        Target positions.
    """
    from ..intraday.rules import range_breakout

    return range_breakout(bars, window, vol_filter)


def _attention_derisk(bars: pd.DataFrame, window: int = 90, threshold: float = 2.5, hold: int = 5) -> pd.Series:
    """Market-attention de-risk (``qlab.radar.research.attention_rule``), imported when first used.

    Args:
        bars: Normalised daily bars.
        window: Baseline days.
        threshold: Spike level, robust standard deviations.
        hold: Days out after a spike.

    Returns:
        Target positions.
    """
    from ..radar.research import attention_rule

    return attention_rule("attention_derisk", bars, window, threshold, hold)


def _crypto_attention_derisk(bars: pd.DataFrame, window: int = 90, threshold: float = 2.5,
                             hold: int = 5) -> pd.Series:
    """Crypto-attention de-risk (``qlab.radar.research.attention_rule``), imported when first used.

    Args:
        bars: Normalised daily bars.
        window: Baseline days.
        threshold: Spike level, robust standard deviations.
        hold: Days out after a spike.

    Returns:
        Target positions.
    """
    from ..radar.research import attention_rule

    return attention_rule("crypto_attention_derisk", bars, window, threshold, hold)


def _tone_derisk(bars: pd.DataFrame, window: int = 90, threshold: float = 2.5, hold: int = 5) -> pd.Series:
    """News-tone de-risk (``qlab.radar.research.tone_rule``), imported when first used.

    Args:
        bars: Normalised daily bars.
        window: Baseline days.
        threshold: Drop level, robust standard deviations.
        hold: Days out after a drop.

    Returns:
        Target positions.
    """
    from ..radar.research import tone_rule

    return tone_rule(bars, window, threshold, hold)


_DERISK_PARAMS = {
    "window": Param(90, 60, 120, 30, "Baseline of the robust z-score, days"),
    "threshold": Param(2.5, 2.0, 3.5, 0.5, "Spike level, robust standard deviations"),
    "hold": Param(5, 1, 9, 2, "Days out of the market after a spike"),
}

REGISTRY: dict[str, Strategy] = {
    s.name: s
    for s in [
        Strategy("buy_and_hold", "buy_and_hold", "Always fully invested. The benchmark.", buy_and_hold),
        Strategy(
            "trend_sma", "trend_sma",
            "Long when the close is above its moving average by an entry band, flat when below by an exit band. "
            "The '200-day rule' from the papers that started this project.",
            trend_sma,
            {
                "window": Param(200, 50, 300, 25, "Moving-average length, bars"),
                "entry_band": Param(0.01, 0.0, 0.05, 0.01, "Entry band above the average (fraction)"),
                "exit_band": Param(0.03, 0.0, 0.06, 0.01, "Exit band below the average (fraction)"),
            },
            "Faber (2007); Hurst, Ooi & Pedersen (2017)",
        ),
        Strategy(
            "sma_cross", "sma_cross", "Long while the fast moving average is above the slow one.", sma_cross,
            {
                "fast": Param(50, 10, 100, 10, "Fast average, bars"),
                "slow": Param(200, 100, 300, 25, "Slow average, bars"),
            },
            "Classic technical rule; Brock, Lakonishok & LeBaron (1992)",
        ),
        Strategy(
            "tsmom", "tsmom", "Hold the sign of the past year's return, skipping the most recent month.", tsmom,
            {
                "lookback": Param(252, 63, 378, 21, "Return window, bars"),
                "skip": Param(21, 0, 42, 7, "Recent bars skipped, bars"),
                "allow_short": Param(0, 0, 1, 1, "1 = short after losses, 0 = flat"),
            },
            "Moskowitz, Ooi & Pedersen (2012)",
        ),
        Strategy(
            "donchian", "donchian", "Long after a new N-bar high, flat after a new M-bar low.", donchian,
            {
                "entry": Param(55, 20, 100, 5, "Breakout window, bars"),
                "exit": Param(20, 10, 50, 5, "Exit window, bars"),
            },
            "Turtle-style breakout; Donchian (1960s)",
        ),
        Strategy(
            "rsi_reversion", "rsi_reversion", "Buy deep short-term oversold readings, exit after the bounce.",
            rsi_reversion,
            {
                "period": Param(2, 2, 6, 1, "RSI length, bars"),
                "buy_below": Param(10, 5, 30, 5, "Entry level"),
                "sell_above": Param(70, 50, 90, 5, "Exit level"),
            },
            "Connors-style RSI(2)",
        ),
        Strategy(
            "insider_timing", "insider_timing",
            "Hold the index while corporate insiders across the market buy more than usual (their net purchase ratio "
            "in SEC Form 4 filings, standardised against its own past); cash otherwise. Pipeline C (M5b).",
            _insider_timing,
            {
                "window": Param(60, 30, 120, 30, "Days of filings per reading"),
                "lookback": Param(730, 365, 1095, 365, "Days of history to standardise against"),
                "threshold": Param(0.0, -1.0, 1.0, 0.5, "Hold above this many standard deviations"),
            },
            "Seyhun (1988); Lakonishok & Lee (2001); Jiang & Zaman (2010)",
            starts="2009-01-02",
        ),
        Strategy(
            "intraday_momentum", "intraday_momentum",
            "Hourly bars only. Hold the last hours of the UTC day when its first hours went up; flat at 00:00 UTC. "
            "Optionally only after a volatile day (the selector). Intraday mode (M9).",
            _intraday_momentum,
            {
                "window": Param(1, 1, 12, 1, "Hours from 00:00 UTC that give the signal"),
                "hold": Param(1, 1, 4, 1, "Last hours of the day held"),
                "vol_filter": Param(0, 0, 1, 1, "1 = only after a day more volatile than usual"),
            },
            "Gao, Han, Li & Zhou (2018); Wen, Bouri, Xu & Zhao (2022)",
        ),
        Strategy(
            "range_breakout", "range_breakout",
            "Hourly bars only. Buy a close above the high of the day's first hours, hold to 00:00 UTC. Optionally "
            "only after a volatile day (the selector). Intraday mode (M9).",
            _range_breakout,
            {
                "window": Param(4, 1, 12, 1, "Hours from 00:00 UTC that set the range"),
                "vol_filter": Param(0, 0, 1, 1, "1 = only after a day more volatile than usual"),
            },
            "Opening-range breakout (Crabel, 1990; Zarattini & Aziz, 2023)",
        ),
        Strategy(
            "attention_derisk", "attention_derisk",
            "Hold the index, and step out for a few days after market attention spikes: Wikipedia views of S&P 500, "
            "Stock market crash and Recession far above their usual level, as published. The radar (M10).",
            _attention_derisk, dict(_DERISK_PARAMS),
            "Da, Engelberg & Gao (2011); Moat et al. (2013); Dimpfl & Jank (2016)",
            starts="2015-11-01",
        ),
        Strategy(
            "crypto_attention_derisk", "crypto_attention_derisk",
            "Hold the coin, and step out for a few days after crypto attention spikes: Wikipedia views of Bitcoin and "
            "Cryptocurrency far above their usual level, as published. The radar (M10).",
            _crypto_attention_derisk, dict(_DERISK_PARAMS),
            "Kristoufek (2013); Urquhart (2018); Da, Engelberg & Gao (2011)",
            starts="2015-11-01",
        ),
        Strategy(
            "tone_derisk", "tone_derisk",
            "Hold the index, and step out for a few days after the tone of stock-market news (GDELT, English sources) "
            "drops far below its usual level, as published. The radar (M10).",
            _tone_derisk, dict(_DERISK_PARAMS),
            "Tetlock (2007); Garcia (2013); Antweiler & Frank (2004)",
            starts="2017-06-01",
        ),
    ]
}
