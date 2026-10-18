"""Price panels: several instruments' daily bars aligned on one calendar.

Two kinds of panel share one type:

- **Static panels** (the ETF universes, M3b) start when every instrument exists and keep only shared dates.
- **Dynamic panels** (the crypto cross-section, M3c) let instruments enter and leave. An availability mask
  (T × N) says which instrument has a bar on which date; prices are NaN where it doesn't trade. Membership is
  point in time: on each date, the top N instruments by trailing 30-day dollar volume, computed from data up to
  that date's close only. Instruments enter after 30 trading days and leave when they fall out of the top N or
  stop trading; delisted pairs stay in the data, so the universe has no survivorship bias. Dynamic panels hold
  drifting positions in the engine (see ``engine``), and a delisted position exits at its last close.

Static-panel design:

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
from ..data.universe import usdt_manifest, usdt_manifest_path


@dataclass(frozen=True)
class Membership:
    """Point-in-time universe rule: the ``top`` instruments by trailing dollar volume on each date.

    Attributes:
        top: Most members on a date.
        window: Trailing window for average dollar volume, days; an instrument needs this many consecutive
            trading days before it can be a member.
        min_members: The panel starts on the first date with at least this many members.
    """

    top: int
    window: int = 30
    min_members: int = 20

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
    "crypto_top30": {
        "members": "binance_usdt",
        "membership": Membership(30),
        "costs": "crypto_xs",
        "description": "The 30 Binance USDT pairs with the most dollar volume over the past 30 days, chosen again "
                       "every day from data up to that day; delisted pairs included.",
    },
    "crypto_top100": {
        "members": "binance_usdt",
        "membership": Membership(100),
        "costs": "crypto_xs",
        "description": "The 100 Binance USDT pairs with the most dollar volume over the past 30 days, chosen again "
                       "every day from data up to that day; delisted pairs included.",
    },
}

MAX_JUMP = 20.0  # a one-day rise above 20x is a reused ticker, not a price move; the series is split there


@dataclass
class Panel:
    """Aligned daily prices for several instruments.

    Attributes:
        name: Universe name, or ``"custom"``.
        ts: Calendar (UTC midnight of each date).
        symbols: Instrument names, one per column.
        sources: Data source per column.
        asset_class: ``"equity"`` or ``"crypto"`` per column.
        open: Adjusted opens, shape ``(T, N)``; NaN where the instrument doesn't trade.
        close: Adjusted closes, shape ``(T, N)``; NaN where the instrument doesn't trade.
        rf: Risk-free return per day for idle cash; zeros for crypto-only panels.
        costs: Cost per unit traded, per column (fraction of notional).
        avail: Whether each instrument has a bar on each date, ``(T, N)``; None means every finite price.
        member: Point-in-time membership, ``(T, N)``, known at each date's close; None means ``avail``.
        dollar_volume: Traded value per day in the quote currency, ``(T, N)``; None means unknown (NaN).
        rule: The membership rule of a dynamic panel; None for a static panel.
        drift: Hold drifting positions between trades in the engine, instead of constant fractions.
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
    avail: np.ndarray | None = None
    member: np.ndarray | None = None
    dollar_volume: np.ndarray | None = None
    rule: Membership | None = None
    drift: bool = False

    def __post_init__(self) -> None:
        """Fill in the masks a static panel leaves out."""
        if self.avail is None:
            with np.errstate(invalid="ignore"):
                self.avail = np.isfinite(self.open) & np.isfinite(self.close) & (self.close > 0)
        if self.member is None:
            self.member = self.avail.copy()
        if self.dollar_volume is None:
            self.dollar_volume = np.full(self.close.shape, np.nan)

    @property
    def n(self) -> int:
        """Number of instruments."""
        return len(self.symbols)

    @property
    def dpy(self) -> int:
        """Trading days per year on this calendar: 365 for crypto-only panels, 252 otherwise."""
        return 365 if all(a == "crypto" for a in self.asset_class) else 252

    @property
    def dynamic(self) -> bool:
        """Whether instruments enter and leave (a membership rule) rather than all trading every date."""
        return self.rule is not None

    def returns(self) -> np.ndarray:
        """Close-to-close returns, shape ``(T, N)``; the first row is zero, and NaN where either day has no bar."""
        r = np.zeros_like(self.close)
        valid = self.avail[1:] & self.avail[:-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            r[1:] = np.where(valid, self.close[1:] / self.close[:-1] - 1.0, np.nan)
        return r

    def log_returns(self) -> np.ndarray:
        """Close-to-close log returns, shape ``(T, N)``; NaN on the first row and where either day has no bar."""
        out = np.full(self.close.shape, np.nan)
        valid = self.avail[1:] & self.avail[:-1]
        with np.errstate(invalid="ignore", divide="ignore"):
            out[1:] = np.where(valid, np.log(self.close[1:] / self.close[:-1]), np.nan)
        return out

    def slice(self, mask: np.ndarray) -> Panel:
        """The panel restricted to some dates.

        Args:
            mask: Boolean per date.

        Returns:
            A new panel with the same instruments; membership stays as computed on the full history, which is
            point in time and so unaffected by the cut.
        """
        return Panel(self.name, self.ts[mask], self.symbols, self.sources, self.asset_class, self.open[mask],
                     self.close[mask], self.rf[mask], self.costs, self.avail[mask], self.member[mask],
                     self.dollar_volume[mask], self.rule, self.drift)

    def with_prices(self, open_: np.ndarray, close: np.ndarray) -> Panel:
        """The same panel with different prices (for permutations and leak checks).

        Args:
            open_: Opens, shape ``(T, N)``.
            close: Closes, shape ``(T, N)``.

        Returns:
            A new panel with the same masks, membership and dollar volume.
        """
        return Panel(self.name, self.ts, self.symbols, self.sources, self.asset_class, open_, close, self.rf,
                     self.costs, self.avail, self.member, self.dollar_volume, self.rule, self.drift)

    def with_data(self, open_: np.ndarray, close: np.ndarray, dollar_volume: np.ndarray,
                  avail: np.ndarray) -> Panel:
        """The same instruments with different prices, volumes and availability, membership recomputed.

        Used by the look-ahead check, which changes the future (prices, volumes, delistings) and asserts that
        no earlier weight moves.

        Args:
            open_: Opens, ``(T, N)``.
            close: Closes, ``(T, N)``.
            dollar_volume: Dollar volume, ``(T, N)``.
            avail: Availability, ``(T, N)``.

        Returns:
            A new panel; membership follows the panel's rule, or equals ``avail`` for a static panel. The first
            ``window - 1`` rows keep their membership: it was computed from days before the panel starts, which
            this panel doesn't hold.
        """
        if self.rule:
            member = membership(dollar_volume, avail, self.rule.top, self.rule.window)
            head = self.rule.window - 1
            member[:head] = self.member[:head] & avail[:head]
        else:
            member = avail.copy()
        return Panel(self.name, self.ts, self.symbols, self.sources, self.asset_class,
                     np.where(avail, open_, np.nan), np.where(avail, close, np.nan), self.rf, self.costs, avail,
                     member, dollar_volume, self.rule, self.drift)


def membership(dollar_volume: np.ndarray, avail: np.ndarray, top: int, window: int = 30) -> np.ndarray:
    """Point-in-time universe: on each date, the ``top`` instruments by average dollar volume over the window.

    Only data up to each date's close is used. An instrument needs a bar on each of the last ``window`` days, so
    a new listing (or one back from a trading halt) waits ``window`` days before it can enter. Ties go to the
    earlier column.

    Args:
        dollar_volume: Traded value per day, ``(T, N)``.
        avail: Availability mask, ``(T, N)``.
        top: Most members on a date.
        window: Trailing window, days.

    Returns:
        Boolean ``(T, N)``, a subset of ``avail``.
    """
    dv = pd.DataFrame(np.where(avail, dollar_volume, np.nan))
    trailing = dv.rolling(window, min_periods=window).mean()
    rank = trailing.rank(axis=1, ascending=False, method="first").to_numpy()
    return avail & np.isfinite(rank) & (rank <= top)


def members(universe: str | list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Instruments of a named universe, or the given list.

    Args:
        universe: Key of ``UNIVERSES`` or a list of ``(source, symbol)``.

    Returns:
        ``(source, symbol)`` pairs; for a dynamic universe, every instrument that may ever be a member.

    Raises:
        KeyError: Unknown universe name.
        FileNotFoundError: A dynamic universe's cross-section hasn't been ingested.
    """
    if not isinstance(universe, str):
        return list(universe)
    spec = UNIVERSES[universe]
    if spec["members"] == "binance_usdt":
        return [("binance", s) for s in usdt_manifest()["kept"]]
    return list(spec["members"])


_CACHE: dict[tuple, tuple] = {}


def _split_jumps(close: np.ndarray, avail: np.ndarray) -> np.ndarray:
    """Availability with a one-day break before every rise above ``MAX_JUMP`` times, so runs never chain across it.

    A ticker reused for a new coin (Binance's LUNA after the 2022 collapse) shows up as an impossible jump. The
    day of the jump becomes unavailable: a position exits at the previous close, and the new coin starts fresh.

    Args:
        close: Closes, ``(T, N)``.
        avail: Availability, ``(T, N)``.

    Returns:
        A new availability mask.
    """
    out = avail.copy()
    with np.errstate(invalid="ignore", divide="ignore"):
        jump = avail[1:] & avail[:-1] & (close[1:] / close[:-1] > MAX_JUMP)
    out[1:][jump] = False
    return out


def _cross_section() -> tuple[pd.DatetimeIndex, list[str], dict[str, np.ndarray]]:
    """Every ingested pair of the crypto cross-section on one calendar, read once per change of the data.

    Returns:
        ``(calendar, symbols, arrays)`` where ``arrays`` holds ``open, close, dv`` (dollar volume) and ``avail``,
        each ``(T, N)``; prices and volume are NaN where a pair has no usable bar.

    Raises:
        FileNotFoundError: The cross-section hasn't been ingested.
    """
    symbols = usdt_manifest()["kept"]
    present = [(s, p) for s in symbols if (p := store.series_path("binance", s, "1d")).exists()]
    stamp = (usdt_manifest_path().stat().st_mtime_ns, max((p.stat().st_mtime_ns for _, p in present), default=0),
             len(present))
    if stamp in _CACHE:
        return _CACHE[stamp]
    frames = {}
    for s, path in present:
        bars = pd.read_parquet(path)
        day = pd.DatetimeIndex(bars["ts"]).tz_convert("UTC").normalize()
        dv = bars["quote_volume"] if "quote_volume" in bars else bars["close"] * bars["volume"]
        frames[s] = pd.DataFrame({"open": bars["open"].to_numpy(float), "close": bars["close"].to_numpy(float),
                                  "volume": bars["volume"].to_numpy(float), "dv": dv.to_numpy(float)}, index=day)
    if not frames:
        raise FileNotFoundError("no crypto cross-section; run `qlab ingest --source binance --universe usdt`")
    cal = frames[next(iter(frames))].index
    for f in frames.values():
        cal = cal.union(f.index)
    cal = cal.sort_values()
    cols = list(frames)

    def grid(key: str) -> np.ndarray:
        return np.column_stack([frames[s][key].reindex(cal).to_numpy(float) for s in cols])

    open_, close, volume, dv = grid("open"), grid("close"), grid("volume"), grid("dv")
    with np.errstate(invalid="ignore"):
        avail = np.isfinite(open_) & np.isfinite(close) & (open_ > 0) & (close > 0) & (volume > 0)
    avail = _split_jumps(close, avail)
    arrays = {"open": np.where(avail, open_, np.nan), "close": np.where(avail, close, np.nan),
              "dv": np.where(avail, dv, np.nan), "avail": avail}
    _CACHE.clear()  # one version of the data is enough; each array is about 3,000 x 700
    _CACHE[stamp] = (cal, cols, arrays)
    return cal, cols, arrays


def _load_dynamic(name: str, spec: dict, cost_override: str | None) -> Panel:
    """Build a dynamic panel from the ingested crypto cross-section.

    Args:
        name: Universe name.
        spec: Its ``UNIVERSES`` entry.
        cost_override: Cost preset for every instrument; None uses the universe's.

    Returns:
        The panel, from the first date with ``min_members`` members, with the instruments that are ever members.

    Raises:
        FileNotFoundError: The cross-section hasn't been ingested.
        ValueError: The universe never has enough members.
    """
    rule: Membership = spec["membership"]
    cal, cols, a = _cross_section()
    member = membership(a["dv"], a["avail"], rule.top, rule.window)
    counts = member.sum(axis=1)
    if not (counts >= rule.min_members).any():
        raise ValueError(f"{name} never has {rule.min_members} members")
    first = int(np.argmax(counts >= rule.min_members))
    idx = np.flatnonzero(member[first:].any(axis=0))  # instruments that are never members can't be held
    preset = COST_PRESETS[cost_override or spec.get("costs", "crypto")]

    def cut(x: np.ndarray) -> np.ndarray:
        return x[first:][:, idx]

    return Panel(name, cal[first:], [cols[i] for i in idx], ["binance"] * len(idx), ["crypto"] * len(idx),
                 cut(a["open"]), cut(a["close"]), np.zeros(len(cal) - first), np.full(len(idx), preset.per_unit),
                 cut(a["avail"]), cut(member), cut(a["dv"]), rule, drift=True)


def load_panel(universe: str | list[tuple[str, str]], cost_override: str | None = None) -> Panel:
    """Build a panel from stored daily bars.

    Args:
        universe: Key of ``UNIVERSES`` or a list of ``(source, symbol)``.
        cost_override: A cost preset applied to every instrument; None uses each one's asset-class preset.

    Returns:
        The panel: for a static universe, from the first date every instrument has a bar; for a dynamic one, from
        the first date with enough members (see ``Membership``).

    Raises:
        FileNotFoundError: An instrument isn't ingested.
        ValueError: The instruments share no dates.
    """
    if isinstance(universe, str) and UNIVERSES[universe]["members"] == "binance_usdt":
        return _load_dynamic(universe, UNIVERSES[universe], cost_override)
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
