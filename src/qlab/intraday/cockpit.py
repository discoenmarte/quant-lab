"""What the Intraday Cockpit shows: the library, its calibration, its verdicts, and the selector's latest reading.

Nothing here runs a backtest. The readings are facts about the latest finished UTC day (its volatility against the
previous 60 days, its first hour's move, the high of its first four hours), not results of a rule. So they add no
trial and don't touch a lockbox. Live intraday decisions would need a live price feed. The public archive
publishes each day after it ends, so none is built until a rule passes the gauntlet (decision 0015).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..backtest.engine import COST_PRESETS
from ..backtest.strategies import REGISTRY
from . import rules

LIBRARY = ("intraday_momentum", "range_breakout")
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def readings(symbol: str) -> dict[str, Any] | None:
    """The selector's inputs and the opening facts for the latest finished UTC day of one coin.

    Args:
        symbol: Binance pair with stored hourly bars.

    Returns:
        ``day``, ``volatile`` (the selector would trade the next day), ``realised_vol`` and ``median_vol`` (daily
        standard deviation of hourly log returns), ``first_hour`` (return of 00:00-01:00 UTC) and ``range_high_4h``,
        or None when no hourly bars are stored.
    """
    from ..backtest import runner

    try:
        bars = runner.load_bars("binance", symbol, "1h")
    except FileNotFoundError:
        return None
    bars = bars.tail(24 * 90).reset_index(drop=True)
    day, hour = rules._calendar(bars)
    counts = np.bincount(day - day.min())
    full = np.nonzero(counts == 24)[0]
    if not len(full):
        return None
    last = int(full[-1] + day.min())
    c = bars["close"].to_numpy(float)
    r = np.diff(np.log(c), prepend=np.nan)
    rv = {int(d): float(np.nanstd(r[day == d], ddof=1)) for d in np.unique(day)}
    past = [rv[d] for d in sorted(rv) if d < last][-rules.VOL_DAYS:]
    median = float(np.median(past)) if past else float("nan")
    on = day == last
    o = bars["open"].to_numpy(float)
    first = float(c[on & (hour == 0)][0] / o[on & (hour == 0)][0] - 1.0) if (on & (hour == 0)).any() else None
    return {"symbol": symbol, "day": str(bars["ts"][on].iloc[0].date()), "volatile": bool(rv[last] > median),
            "realised_vol": rv[last], "median_vol": median, "first_hour": first,
            "range_high_4h": float(bars["high"].to_numpy(float)[on & (hour < 4)].max())}


def overview() -> dict[str, Any]:
    """Everything the Intraday Cockpit page shows.

    Returns:
        ``rules`` (name, description, reference, parameters, grid size), ``round_trip_bps`` (crypto costs of one day's
        trade), ``calibration`` (the latest hourly calibration report's noise and power rows, or None), ``cards``
        (gauntlet cards of the intraday questions) and ``readings`` per coin.
    """
    from ..gauntlet import calibration, pipeline

    lib = []
    for name in LIBRARY:
        s = REGISTRY[name]
        grid = 1
        for p in s.params.values():
            grid *= int(round((p.high - p.low) / p.step)) + 1
        lib.append({"name": name, "description": s.description, "reference": s.reference, "grid": grid,
                    "params": {k: {"default": p.default, "low": p.low, "high": p.high, "doc": p.doc}
                               for k, p in s.params.items()}})
    cal = next((r for r in calibration.list_reports() if (r.get("settings") or {}).get("hourly_reps")), None)
    if cal:
        cal = {"id": cal["id"], "created_at": cal["created_at"], "headline": cal.get("headline"),
               "noise": [n for n in cal["noise"] if n["family"].startswith("hourly/")],
               "power": [p for p in cal["power"] if p["family"].startswith("hourly/")]}
    cards = [c for c in pipeline.list_cards(500) if c["question"].split(":")[0] in LIBRARY]
    m = COST_PRESETS["crypto"]
    return {"rules": lib, "round_trip_bps": 2 * m.per_unit * 1e4, "calibration": cal, "cards": cards,
            "readings": [x for x in (readings(s) for s in SYMBOLS) if x]}
