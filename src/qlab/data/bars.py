"""The bar contract shared by every price source.

Daily bars are keyed by session date at 00:00 UTC; intraday bars by their open time in UTC. Prices are in
the instrument's quote currency and volume is in base units (shares or coins). ``adj_close`` is present when
the source provides a dividend- and split-adjusted close.
"""

from __future__ import annotations

import pandas as pd

BAR_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
PRICE_COLUMNS = ["open", "high", "low", "close"]


def normalize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Return bars in canonical form: UTC timestamps, float prices, sorted, one row per timestamp.

    Args:
        df: Bars with at least the ``BAR_COLUMNS``; ``ts`` may be naive (read as UTC) or tz-aware.

    Returns:
        A new DataFrame with ``ts`` as tz-aware UTC, numeric columns as float64, rows sorted by ``ts`` and
        duplicate timestamps reduced to the last occurrence.

    Raises:
        ValueError: A required column is missing.
    """
    missing = [c for c in BAR_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bars are missing columns: {missing}")
    out = df.copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    for col in out.columns:
        if col != "ts":
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    out = out.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)
    return out


def adjusted(df: pd.DataFrame) -> pd.DataFrame:
    """Total-return-adjusted OHLC, for computing returns that include dividends.

    Open, high and low are scaled by the same factor as the close (``adj_close / close``). Use these for
    returns; use the raw prices for anything that models an order price.

    Args:
        df: Normalised bars, optionally with ``adj_close``.

    Returns:
        A copy with adjusted ``open, high, low, close``. Unchanged when ``adj_close`` is absent.
    """
    if "adj_close" not in df.columns:
        return df.copy()
    out = df.copy()
    factor = (out["adj_close"] / out["close"]).where(out["close"] > 0)
    for col in PRICE_COLUMNS:
        out[col] = out[col] * factor
    return out
