"""Price panels: several instruments' daily bars aligned on one calendar.

Design:

- **One calendar, from the day every instrument exists.** The panel starts at the latest first date among its
  instruments and keeps only dates where all of them have a bar. No instrument enters halfway, so strategies never
  see an asset appear (an implicit look-ahead about which funds survive) and the permutation test can shuffle
  whole days. Choose a smaller universe for a longer history.
- **Mixed calendars.** ETFs trade on weekdays, crypto every day. When both are present, the panel uses the dates
  they share; a crypto weekend becomes part of Monday's overnight gap, and crypto positions change on weekdays
  only. Timing stays honest: every bar's close on date d (ETF 21:00 UTC, crypto 00:00 UTC of d+1) is known before
  the next bar's open, where the engine fills.
- Prices are dividend-adjusted, because the panel is for returns; order prices come from raw bars (M7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..backtest.engine import COST_PRESETS
from ..data import store
from ..data.bars import adjusted
from ..data.ingest import SOURCES

ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ", "HYG", "LQD",
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB"]

UNIVERSES: dict[str, dict] = {
    "multi_asset": {
        "members": [("yahoo", s) for s in ["SPY", "EFA", "EEM", "IWM", "TLT", "IEF", "LQD", "HYG", "GLD", "DBC",
                                            "VNQ"]],
        "description": "Eleven asset classes: US large and small caps, developed and emerging equities, long and "
                       "medium Treasuries, investment-grade and high-yield credit, gold, commodities, real estate.",
    },
    "asset_classes_long": {
        "members": [("yahoo", s) for s in ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ"]],
        "description": "Seven asset classes with history from 2004: a longer sample with less breadth.",
    },
    "sectors": {
        "members": [("yahoo", s) for s in ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB"]],
        "description": "The nine original S&P 500 sector funds, from December 1998.",
    },
    "all_etfs": {
        "members": [("yahoo", s) for s in ETFS],
        "description": "Every ETF in the lab's universe.",
    },
    "crypto": {
        "members": [("binance", "BTCUSDT"), ("binance", "ETHUSDT")],
        "description": "Bitcoin and Ether, every calendar day.",
    },
    "etfs_and_crypto": {
        "members": [("yahoo", s) for s in ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ"]]
        + [("binance", "BTCUSDT"), ("binance", "ETHUSDT")],
        "description": "Eight asset classes plus Bitcoin and Ether, on the ETF calendar, from 2017.",
    },
}


@dataclass
class Panel:
    """Aligned daily prices for several instruments.

    Attributes:
        name: Universe name, or ``"custom"``.
        ts: Calendar (UTC midnight of each date).
        symbols: Instrument names, one per column.
        sources: Data source per column.
        asset_class: ``"equity"`` or ``"crypto"`` per column.
        open: Adjusted opens, shape ``(T, N)``.
        close: Adjusted closes, shape ``(T, N)``.
        rf: Risk-free return per day for idle cash; zeros for crypto-only panels.
        costs: Cost per unit traded, per column (fraction of notional).
    """

    name: str
    ts: pd.DatetimeIndex
    symbols: list[str]
    sources: list[str]
    asset_class: list[str]
    open: np.ndarray
    close: np.ndarray
    rf: np.ndarray
    costs: np.ndarray = field(default_factory=lambda: np.zeros(0))

    @property
    def n(self) -> int:
        """Number of instruments."""
        return len(self.symbols)

    @property
    def dpy(self) -> int:
        """Trading days per year on this calendar: 365 for crypto-only panels, 252 otherwise."""
        return 365 if all(a == "crypto" for a in self.asset_class) else 252

    def returns(self) -> np.ndarray:
        """Close-to-close returns, shape ``(T, N)``; the first row is zero."""
        r = np.zeros_like(self.close)
        r[1:] = self.close[1:] / self.close[:-1] - 1.0
        return r

    def slice(self, mask: np.ndarray) -> Panel:
        """The panel restricted to some dates.

        Args:
            mask: Boolean per date.

        Returns:
            A new panel with the same instruments.
        """
        return Panel(self.name, self.ts[mask], self.symbols, self.sources, self.asset_class, self.open[mask],
                     self.close[mask], self.rf[mask], self.costs)

    def with_prices(self, open_: np.ndarray, close: np.ndarray) -> Panel:
        """The same panel with different prices (for permutations and leak checks).

        Args:
            open_: Opens, shape ``(T, N)``.
            close: Closes, shape ``(T, N)``.

        Returns:
            A new panel.
        """
        return Panel(self.name, self.ts, self.symbols, self.sources, self.asset_class, open_, close, self.rf,
                     self.costs)


def members(universe: str | list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Instruments of a named universe, or the given list.

    Args:
        universe: Key of ``UNIVERSES`` or a list of ``(source, symbol)``.

    Returns:
        ``(source, symbol)`` pairs.

    Raises:
        KeyError: Unknown universe name.
    """
    return list(UNIVERSES[universe]["members"]) if isinstance(universe, str) else list(universe)


def load_panel(universe: str | list[tuple[str, str]], cost_override: str | None = None) -> Panel:
    """Build a panel from stored daily bars.

    Args:
        universe: Key of ``UNIVERSES`` or a list of ``(source, symbol)``.
        cost_override: A cost preset applied to every instrument; None uses each one's asset-class preset.

    Returns:
        The panel, from the first date every instrument has a bar.

    Raises:
        FileNotFoundError: An instrument isn't ingested.
        ValueError: The instruments share no dates.
    """
    pairs = members(universe)
    frames = []
    for source, symbol in pairs:
        bars = adjusted(store.read_series(source, symbol, "1d"))
        day = pd.DatetimeIndex(bars["ts"]).tz_convert("UTC").normalize()
        frames.append(pd.DataFrame({"open": bars["open"].to_numpy(), "close": bars["close"].to_numpy()}, index=day))
    common = frames[0].index
    for f in frames[1:]:
        common = common.intersection(f.index)
    common = common.sort_values()
    if len(common) == 0:
        raise ValueError("the instruments share no dates")
    classes = [SOURCES[s].asset_class if s in SOURCES else "equity" for s, _ in pairs]
    open_ = np.column_stack([f.loc[common, "open"].to_numpy(float) for f in frames])
    close = np.column_stack([f.loc[common, "close"].to_numpy(float) for f in frames])
    rf = np.zeros(len(common))
    if any(c == "equity" for c in classes):
        try:
            ff = store.read_series("french", "FF3", "1d").set_index("ts")["rf"]
            ff.index = pd.DatetimeIndex(ff.index).tz_convert("UTC").normalize()
            rf = ff.reindex(common).ffill().fillna(0.0).to_numpy(float)
        except FileNotFoundError:
            pass
    preset = {"crypto": COST_PRESETS["crypto"], "equity": COST_PRESETS["etf"]}
    costs = np.array([(COST_PRESETS[cost_override] if cost_override else preset.get(c, COST_PRESETS["etf"])).per_unit
                      for c in classes])
    name = universe if isinstance(universe, str) else "custom"
    return Panel(name, common, [s for _, s in pairs], [src for src, _ in pairs], classes, open_, close, rf, costs)


def cost_model_of(panel: Panel) -> dict[str, float]:
    """Average cost per unit traded across the panel, in basis points, for reports.

    Args:
        panel: A panel.

    Returns:
        ``{"avg_bps": ..., "min_bps": ..., "max_bps": ...}``.
    """
    bps = panel.costs * 1e4
    return {"avg_bps": float(bps.mean()), "min_bps": float(bps.min()), "max_bps": float(bps.max())}
