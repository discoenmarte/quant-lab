"""Binance public data archive (data.binance.vision): spot klines.

Monthly zip files cover finished months; the current month (and a just-finished month that hasn't been
published yet) comes from daily files. Every zip has a ``.CHECKSUM`` file with its SHA-256, which is verified
before the zip is kept. Downloaded zips are cached under ``raw/binance`` because published files never change.

Quirk: spot kline timestamps are in milliseconds up to 2024 and in **microseconds from 2025-01-01**. The
parser detects the unit per row.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from ... import config
from ..bars import normalize_bars
from ..http import SourceError, get

BASE = "https://data.binance.vision/data/spot"
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
FIRST_MONTH = "2017-08"
WORKERS = 8  # parallel downloads; the archive is a static file host


def _cache_path(kind: str, symbol: str, interval: str, stamp: str) -> Path:
    """Local path of a cached archive file.

    Args:
        kind: ``"monthly"`` or ``"daily"``.
        symbol: Trading pair, e.g. ``"BTCUSDT"``.
        interval: Kline interval, e.g. ``"1d"`` or ``"1h"``.
        stamp: ``YYYY-MM`` for monthly files, ``YYYY-MM-DD`` for daily files.

    Returns:
        ``raw/binance/<kind>/<symbol>/<interval>/<symbol>-<interval>-<stamp>.zip``.
    """
    return config.raw_dir() / "binance" / kind / symbol / interval / f"{symbol}-{interval}-{stamp}.zip"


def _download(kind: str, symbol: str, interval: str, stamp: str) -> bytes | None:
    """Fetch one archive zip, verify its checksum and cache it.

    Args:
        kind: ``"monthly"`` or ``"daily"``.
        symbol: Trading pair.
        interval: Kline interval.
        stamp: Month or day stamp of the file.

    Returns:
        The zip bytes, or None when the file isn't published.

    Raises:
        SourceError: The checksum doesn't match.
    """
    path = _cache_path(kind, symbol, interval, stamp)
    if path.exists():
        return path.read_bytes()
    url = f"{BASE}/{kind}/klines/{symbol}/{interval}/{symbol}-{interval}-{stamp}.zip"
    blob = get(url, allow_404=True)
    if blob is None:
        return None
    checksum = get(url + ".CHECKSUM", allow_404=True)
    if checksum:
        expected = checksum.decode().split()[0].lower()
        actual = hashlib.sha256(blob).hexdigest()
        if actual != expected:
            raise SourceError(f"checksum mismatch for {url}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return blob


def parse_klines(blob: bytes) -> pd.DataFrame:
    """Parse one kline zip (or its CSV) into bars.

    Args:
        blob: Zip bytes from the archive, or raw CSV bytes.

    Returns:
        Bars with ``ts, open, high, low, close, volume, trades``. ``ts`` is the candle's open time in UTC.
    """
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            text = zf.read(zf.namelist()[0])
    else:
        text = blob
    df = pd.read_csv(io.BytesIO(text), header=None, names=KLINE_COLUMNS)
    if not str(df.iloc[0, 0]).strip().isdigit():  # some archive files carry a header row
        df = df.iloc[1:]
    open_time = pd.to_numeric(df["open_time"]).astype("int64")
    # Microseconds from 2025-01-01, milliseconds before: anything above 1e14 can't be milliseconds.
    millis = open_time.where(open_time < 10**14, open_time // 1000)
    bars = pd.DataFrame(
        {
            "ts": pd.to_datetime(millis, unit="ms", utc=True),
            "open": df["open"],
            "high": df["high"],
            "low": df["low"],
            "close": df["close"],
            "volume": df["volume"],
            "trades": df["trades"],
        }
    )
    return normalize_bars(bars)


def _months(start: str, end: str) -> list[str]:
    """Months from ``start`` to ``end`` inclusive.

    Args:
        start: ``YYYY-MM``.
        end: ``YYYY-MM``.

    Returns:
        List of ``YYYY-MM`` stamps.
    """
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _days_of_month(month: str, until: date) -> list[str]:
    """Days of a month up to and including ``until``.

    Args:
        month: ``YYYY-MM``.
        until: Last day to include.

    Returns:
        List of ``YYYY-MM-DD`` stamps.
    """
    y, m = map(int, month.split("-"))
    d = date(y, m, 1)
    out = []
    while d.month == m and d <= until:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def fetch(symbol: str, interval: str, start_month: str = FIRST_MONTH, today: date | None = None) -> pd.DataFrame:
    """Full kline history for one pair and interval.

    Args:
        symbol: Trading pair, e.g. ``"BTCUSDT"``.
        interval: ``"1d"``, ``"1h"`` or any interval the archive publishes.
        start_month: First month to fetch (``YYYY-MM``); months before the pair existed are skipped.
        today: Reference date in UTC. None means now. Days up to yesterday are fetched.

    Returns:
        Normalised bars from the first available candle to yesterday.

    Raises:
        SourceError: Nothing could be downloaded at all.
    """
    today = today or datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    months = _months(start_month, yesterday.strftime("%Y-%m"))
    current = today.strftime("%Y-%m")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        monthly = dict(
            zip(
                months,
                pool.map(lambda m: None if m == current else _download("monthly", symbol, interval, m), months),
                strict=True,
            )
        )
        # Months without a published monthly file (the current one, or one finished days ago) use daily files.
        days = [d for m in months if monthly[m] is None for d in _days_of_month(m, yesterday)]
        daily = list(pool.map(lambda d: _download("daily", symbol, interval, d), days))

    blobs = [b for b in monthly.values() if b is not None] + [b for b in daily if b is not None]
    if not blobs:
        raise SourceError(f"no Binance klines found for {symbol} {interval}")
    return normalize_bars(pd.concat([parse_klines(b) for b in blobs], ignore_index=True))
