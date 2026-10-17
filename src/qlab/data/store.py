"""Parquet storage for series, plus the DuckDB catalog that indexes them.

Layout: ``curated/<source>/<symbol>/<interval>.parquet`` for bars and factors, and
``curated/<source>/<symbol>/events.parquet`` for dividends and splits. Every write records the row count,
first and last timestamps and a content hash in the ``series`` table.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from .. import config, db
from ..timeutil import naive_utc


def series_path(source: str, symbol: str, interval: str) -> Path:
    """Location of one series file.

    Args:
        source: Source name, e.g. ``"binance"``.
        symbol: Instrument, e.g. ``"BTCUSDT"``.
        interval: Bar interval, e.g. ``"1d"``.

    Returns:
        ``curated/<source>/<symbol>/<interval>.parquet``.
    """
    return config.curated_dir() / source / symbol / f"{interval}.parquet"


def events_path(source: str, symbol: str) -> Path:
    """Location of a symbol's corporate events file.

    Args:
        source: Source name.
        symbol: Instrument.

    Returns:
        ``curated/<source>/<symbol>/events.parquet``.
    """
    return config.curated_dir() / source / symbol / "events.parquet"


def data_hash(df: pd.DataFrame) -> str:
    """Content fingerprint of a DataFrame, independent of how the file was written.

    Args:
        df: Any DataFrame.

    Returns:
        First 16 hex characters of the SHA-256 of the row hashes.
    """
    row_hashes = pd.util.hash_pandas_object(df, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()[:16]


def write_series(source: str, symbol: str, interval: str, df: pd.DataFrame, kind: str = "bars") -> dict[str, Any]:
    """Write a series and update its catalog entry.

    Args:
        source: Source name.
        symbol: Instrument.
        interval: Bar interval.
        df: Normalised data with a ``ts`` column.
        kind: ``"bars"`` or ``"factors"``.

    Returns:
        The catalog entry: ``source, symbol, interval, kind, n_rows, first_ts, last_ts, hash, path``.
    """
    path = series_path(source, symbol, interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    entry = {
        "source": source,
        "symbol": symbol,
        "interval": interval,
        "kind": kind,
        "n_rows": int(len(df)),
        "first_ts": df["ts"].min().to_pydatetime().replace(tzinfo=None) if len(df) else None,
        "last_ts": df["ts"].max().to_pydatetime().replace(tzinfo=None) if len(df) else None,
        "hash": data_hash(df),
        "path": str(path),
    }
    with db.connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO series
               (source, symbol, interval, first_ts, last_ts, n_rows, path, updated_at, kind, data_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [source, symbol, interval, entry["first_ts"], entry["last_ts"], entry["n_rows"], str(path),
             naive_utc(), kind, entry["hash"]],
        )
    return entry


def read_series(source: str, symbol: str, interval: str) -> pd.DataFrame:
    """Load a stored series.

    Args:
        source: Source name.
        symbol: Instrument.
        interval: Bar interval.

    Returns:
        The stored DataFrame with ``ts`` as UTC timestamps.

    Raises:
        FileNotFoundError: The series hasn't been ingested.
    """
    path = series_path(source, symbol, interval)
    if not path.exists():
        raise FileNotFoundError(f"no data for {source}/{symbol}/{interval}; run `qlab ingest --source {source}`")
    return pd.read_parquet(path)


def write_events(source: str, symbol: str, events: pd.DataFrame) -> None:
    """Store dividends and splits for a symbol.

    Args:
        source: Source name.
        symbol: Instrument.
        events: Columns ``ts, kind, value``.
    """
    path = events_path(source, symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    events.to_parquet(path, index=False)


def read_events(source: str, symbol: str) -> pd.DataFrame:
    """Load stored dividends and splits.

    Args:
        source: Source name.
        symbol: Instrument.

    Returns:
        Columns ``ts, kind, value``; empty when the source has no events for the symbol.
    """
    path = events_path(source, symbol)
    if not path.exists():
        return pd.DataFrame(columns=["ts", "kind", "value"])
    return pd.read_parquet(path)


def list_series() -> list[dict[str, Any]]:
    """Every stored series with its open quality-issue counts.

    Returns:
        Catalog rows plus ``n_errors`` and ``n_warnings``, ordered by source, symbol and interval.
    """
    return db.rows(
        """SELECT s.source, s.symbol, s.interval, s.kind, s.first_ts, s.last_ts, s.n_rows, s.updated_at,
                  s.data_hash,
                  count(q.check_name) FILTER (WHERE q.severity = 'error') AS n_errors,
                  count(q.check_name) FILTER (WHERE q.severity = 'warn') AS n_warnings
           FROM series s
           LEFT JOIN quality_issues q
             ON q.source = s.source AND q.symbol = s.symbol AND q.interval = s.interval
           GROUP BY ALL
           ORDER BY s.source, s.symbol, s.interval"""
    )


def coverage(interval: str = "1d") -> list[dict[str, Any]]:
    """Rows per symbol per calendar year, for the coverage heatmap.

    Args:
        interval: Which interval to summarise.

    Returns:
        Records ``{"source", "symbol", "year", "n_rows"}``.
    """
    out: list[dict[str, Any]] = []
    for s in db.rows("SELECT source, symbol, path FROM series WHERE interval = ? AND kind = 'bars'", [interval]):
        if not Path(s["path"]).exists():
            continue
        df = pd.read_parquet(s["path"], columns=["ts"])
        counts = df["ts"].dt.year.value_counts().sort_index()
        out.extend(
            {"source": s["source"], "symbol": s["symbol"], "year": int(y), "n_rows": int(n)}
            for y, n in counts.items()
        )
    return out
