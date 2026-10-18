"""Binance public data archive (data.binance.vision): spot klines, and the list of every pair it holds.

Monthly zip files cover finished months; the current month (and a just-finished month that hasn't been
published yet) comes from daily files. Downloaded zips are cached under ``raw/binance``.

The archive is an S3 bucket, so its listing says which files exist and gives each file's MD5 (its ETag):
``fetch`` downloads only listed files instead of probing every month since 2017, verifies each zip against the
listed MD5, and re-downloads a cached zip whose MD5 no longer matches (the archive occasionally republishes a
month). When a listing has no plain MD5, the zip's published ``.CHECKSUM`` file (SHA-256) is verified instead.
Files come from the bucket's own endpoint, which answers about twice as fast from the owner's network as the
CDN name; ``prefetch`` warms the cache for many pairs at once. The archive keeps pairs after Binance delists them, which
is what makes a survivorship-free cross-section possible (``usdt_universe``).

Quirk: spot kline timestamps are in milliseconds up to 2024 and in **microseconds from 2025-01-01**. The
parser detects the unit per row.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import pandas as pd

from ... import config
from ..bars import normalize_bars
from ..http import SourceError, get

BASE = "https://data.binance.vision/data/spot"
BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
S3_NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
FIRST_MONTH = "2017-08"
WORKERS = 8  # parallel downloads; the archive is a static file host, so a few at a time is polite

# Pairs left out of the USDT cross-section, by base asset. Each has a reason a momentum or size rule shouldn't
# trade it as a crypto asset: it is pegged to a currency or to gold, or it duplicates another coin.
STABLECOINS = {
    "AEUR", "AUD", "BFUSD", "BKRW", "BUSD", "DAI", "EUR", "EURI", "FDUSD", "GBP", "KGST", "PAX", "RLUSD", "SUSD",
    "TUSD", "U", "USD1", "USDC", "USDE", "USDP", "USDS", "USDSB", "USDSOLD", "UST", "USTC", "XUSD",
}
PEGGED = {"PAXG", "XAUT"}  # gold tokens
WRAPPED = {"BETH", "BNSOL", "WBETH", "WBTC"}  # wrapped or staked copies of ETH, SOL and BTC
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
# Binance's tokenized equities (AAPLB, NVDAB, SPYB, ...) are named <ticker>B and were all listed from June 2026.
TOKENIZED_FROM = "2026-06"


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


def list_bucket(prefix: str) -> tuple[list[str], dict[str, str]]:
    """Everything directly under a prefix of the archive bucket, following every page of the listing.

    S3 returns at most 1,000 entries per page; each further page starts after the previous page's
    ``NextMarker`` (or its last entry when the marker is absent).

    Args:
        prefix: Bucket prefix, e.g. ``"data/spot/monthly/klines/"``.

    Returns:
        ``(prefixes, objects)``: sub-prefixes one level down, and object keys mapped to their ETag (quotes
        stripped; the MD5 of the file for single-part uploads).

    Raises:
        SourceError: The listing can't be fetched or parsed.
    """
    prefixes: list[str] = []
    objects: dict[str, str] = {}
    marker = ""
    while True:
        blob = get(f"{BUCKET}?{urlencode({'delimiter': '/', 'prefix': prefix, 'marker': marker})}")
        try:
            root = ElementTree.fromstring(blob)
        except ElementTree.ParseError as err:
            raise SourceError(f"unreadable archive listing for {prefix}") from err
        page = [p.findtext("s:Prefix", namespaces=S3_NS) for p in root.findall("s:CommonPrefixes", S3_NS)]
        prefixes += page
        for c in root.findall("s:Contents", S3_NS):
            objects[c.findtext("s:Key", namespaces=S3_NS)] = (c.findtext("s:ETag", namespaces=S3_NS) or "").strip('"')
        if root.findtext("s:IsTruncated", namespaces=S3_NS) != "true":
            return prefixes, objects
        marker = root.findtext("s:NextMarker", namespaces=S3_NS) or max(page + list(objects))


def list_symbols() -> list[str]:
    """Every spot pair with monthly kline files in the archive, including delisted ones.

    Returns:
        Pair names, e.g. ``["0GBNB", ..., "ZRXUSDT"]``, sorted.

    Raises:
        SourceError: The listing can't be fetched.
    """
    root = "data/spot/monthly/klines/"
    prefixes, _ = list_bucket(root)
    return sorted(p[len(root):].rstrip("/") for p in prefixes)


@cache
def listed(symbol: str, interval: str, kind: str = "monthly", month: str = "") -> dict[str, str]:
    """The archive files of one pair and interval, by stamp.

    Cached for the life of the process, so the universe filter and the download share one listing per pair.

    Args:
        symbol: Trading pair.
        interval: Kline interval.
        kind: ``"monthly"`` or ``"daily"``.
        month: For daily files, only this ``YYYY-MM``; empty lists everything.

    Returns:
        ``{stamp: etag}`` for every listed zip; stamps are ``YYYY-MM`` (monthly) or ``YYYY-MM-DD`` (daily).

    Raises:
        SourceError: The listing can't be fetched.
    """
    folder = f"data/spot/{kind}/klines/{symbol}/{interval}/"
    _, objects = list_bucket(folder + (f"{symbol}-{interval}-{month}" if month else ""))
    head = f"{folder}{symbol}-{interval}-"
    return {k[len(head):-4]: etag for k, etag in objects.items() if k.startswith(head) and k.endswith(".zip")}


def _download(kind: str, symbol: str, interval: str, stamp: str, etag: str = "") -> bytes | None:
    """Fetch one archive zip, verify it and cache it.

    Args:
        kind: ``"monthly"`` or ``"daily"``.
        symbol: Trading pair.
        interval: Kline interval.
        stamp: Month or day stamp of the file.
        etag: The listed ETag. A plain one is the file's MD5: a cached file must match it (or is downloaded
            again) and a download must match it. Empty or multipart (with ``-``) trusts the cache and verifies
            downloads against the published SHA-256 ``.CHECKSUM`` file instead.

    Returns:
        The zip bytes, or None when the file isn't published.

    Raises:
        SourceError: The download doesn't match its checksum.
    """
    md5 = etag if etag and "-" not in etag else ""
    path = _cache_path(kind, symbol, interval, stamp)
    if path.exists():
        blob = path.read_bytes()
        if not md5 or hashlib.md5(blob).hexdigest() == md5:
            return blob
    key = f"data/spot/{kind}/klines/{quote(symbol)}/{interval}/{quote(symbol)}-{interval}-{stamp}.zip"
    blob = get(f"{BUCKET}/{key}", allow_404=True)
    if blob is None:
        return None
    if md5:
        if hashlib.md5(blob).hexdigest() != md5:
            raise SourceError(f"MD5 mismatch for {key}")
    else:
        checksum = get(f"{BUCKET}/{key}.CHECKSUM", allow_404=True)
        if checksum and hashlib.sha256(blob).hexdigest() != checksum.decode().split()[0].lower():
            raise SourceError(f"checksum mismatch for {key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    return blob


def parse_klines(blob: bytes) -> pd.DataFrame:
    """Parse one kline zip (or its CSV) into bars.

    Args:
        blob: Zip bytes from the archive, or raw CSV bytes.

    Returns:
        Bars with ``ts, open, high, low, close, volume, quote_volume, trades``. ``ts`` is the candle's open
        time in UTC; ``volume`` is in the base coin and ``quote_volume`` in the quote currency (USDT for the
        cross-section, so it is the day's dollar volume).
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
            "quote_volume": df["quote_volume"],
            "trades": df["trades"],
        }
    )
    return normalize_bars(bars)


def _previous_month(month: str) -> str:
    """The month before ``month``.

    Args:
        month: ``YYYY-MM``.

    Returns:
        ``YYYY-MM``.
    """
    y, m = map(int, month.split("-"))
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def _plan(symbol: str, interval: str, start_month: str, today: date) -> list[tuple[str, str, str]]:
    """The files that make up one pair's history, from the archive listing.

    Finished months come from monthly files. Months after the last monthly file come from daily files, but only
    for the current and the previous month (a monthly file appears a few days after its month ends); a pair
    whose last monthly file is older than that was delisted, and has no daily files to look for.

    Args:
        symbol: Trading pair.
        interval: Kline interval.
        start_month: First month (``YYYY-MM``).
        today: Reference date in UTC; days up to yesterday are included.

    Returns:
        ``(kind, stamp, etag)`` per file, monthly files first.
    """
    yesterday = (today - timedelta(days=1)).isoformat()
    current = today.strftime("%Y-%m")
    previous = _previous_month(current)
    monthly = {m: e for m, e in listed(symbol, interval, "monthly").items() if start_month <= m < current}
    last = max(monthly) if monthly else None
    daily: dict[str, str] = {}
    if last is None or last >= _previous_month(previous):
        for month in (previous, current):
            if last is None or month > last:
                daily.update({d: e for d, e in listed(symbol, interval, "daily", month).items() if d <= yesterday})
    return [("monthly", m, e) for m, e in sorted(monthly.items())] + [("daily", d, e) for d, e in sorted(daily.items())]


def fetch(symbol: str, interval: str, start_month: str = FIRST_MONTH, today: date | None = None) -> pd.DataFrame:
    """Full kline history for one pair and interval, from the files the archive lists (see ``_plan``).

    Args:
        symbol: Trading pair, e.g. ``"BTCUSDT"``.
        interval: ``"1d"``, ``"1h"`` or any interval the archive publishes.
        start_month: First month to fetch (``YYYY-MM``).
        today: Reference date in UTC. None means now. Days up to yesterday are fetched.

    Returns:
        Normalised bars from the first available candle to yesterday (or to the delisting).

    Raises:
        SourceError: Nothing could be downloaded at all, or a checksum doesn't match.
    """
    files = _plan(symbol, interval, start_month, today or datetime.now(UTC).date())
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        blobs = list(pool.map(lambda f: _download(f[0], symbol, interval, f[1], f[2]), files))
    blobs = [b for b in blobs if b is not None]
    if not blobs:
        raise SourceError(f"no Binance klines found for {symbol} {interval}")
    return normalize_bars(pd.concat([parse_klines(b) for b in blobs], ignore_index=True))


def prefetch(symbols: list[str], interval: str, log=None, today: date | None = None) -> int:
    """Download every listed file of many pairs into the cache, a few at a time, so ingesting them is fast.

    Args:
        symbols: Trading pairs.
        interval: Kline interval.
        log: Receives progress lines; None is silent.
        today: Reference date in UTC. None means now.

    Returns:
        Number of files in the plan (cached ones are verified, not downloaded again).

    Raises:
        SourceError: A listing can't be fetched.
    """
    say = log or (lambda _msg: None)
    day = today or datetime.now(UTC).date()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        plans = list(pool.map(lambda s: _plan(s, interval, FIRST_MONTH, day), symbols))
    files = [(s, *f) for s, plan in zip(symbols, plans, strict=True) for f in plan]
    say(f"{len(files)} files for {len(symbols)} pairs")

    def one(item: tuple[str, str, str, str]) -> None:
        s, kind, stamp, etag = item
        try:
            _download(kind, s, interval, stamp, etag)
        except SourceError as err:  # fetch retries the file and reports it per pair
            say(f"{s} {stamp}: {err}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, _ in enumerate(pool.map(one, files), 1):
            if i % 2000 == 0:
                say(f"  {i}/{len(files)} files")
    return len(files)


def exclusion_reason(base: str, bases: set[str], first_month: str | None = None) -> str | None:
    """Why a USDT pair's base asset is left out of the crypto cross-section, if it is.

    Args:
        base: Base asset, e.g. ``"BTC"`` for ``BTCUSDT``.
        bases: Every base asset with a USDT pair, to recognise leveraged tokens (``BTCUP`` is leveraged because
            ``BTC`` is listed; ``JUP`` and ``SYRUP`` are coins because ``J`` and ``SYR`` aren't).
        first_month: First monthly file of the pair (``YYYY-MM``), for the tokenized-equity rule; None skips it.

    Returns:
        ``"stablecoin"``, ``"pegged"``, ``"wrapped"``, ``"leveraged"``, ``"tokenized_equity"`` or None to keep it.
    """
    if base in STABLECOINS:
        return "stablecoin"
    if base in PEGGED:
        return "pegged"
    if base in WRAPPED:
        return "wrapped"
    if base in LEVERAGED_SUFFIXES:  # BULLUSDT and BEARUSDT were 3x Bitcoin tokens
        return "leveraged"
    if any(base.endswith(k) and base[: -len(k)] in bases for k in LEVERAGED_SUFFIXES):
        return "leveraged"
    if first_month and base.endswith("B") and len(base) > 2 and first_month >= TOKENIZED_FROM:
        return "tokenized_equity"
    return None


def usdt_universe(interval: str = "1d", log=None) -> dict:
    """Every USDT spot pair the archive holds, split into the cross-section and the pairs left out.

    Delisted pairs stay in: their files remain in the archive after the delisting, so the cross-section is free
    of survivorship bias as long as each date's universe is chosen from data up to that date (see
    ``portfolio.panel``).

    Args:
        interval: Interval whose monthly files decide each pair's first and last month.
        log: Receives progress lines; None is silent.

    Returns:
        ``{"listed_at", "n_pairs", "kept": [symbol, ...], "excluded": {symbol: reason}, "months": {symbol:
        [first, last]}}``.

    Raises:
        SourceError: The archive listing can't be fetched.
    """
    say = log or (lambda _msg: None)
    pairs = [s for s in list_symbols() if s.endswith("USDT") and len(s) > 4]
    bases = {s[:-4] for s in pairs}
    say(f"archive lists {len(pairs)} USDT pairs")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        files = dict(zip(pairs, pool.map(lambda s: listed(s, interval, "monthly"), pairs), strict=True))
    kept, excluded, months = [], {}, {}
    for s in pairs:
        stamps = sorted(files[s])
        months[s] = [stamps[0], stamps[-1]] if stamps else [None, None]
        reason = exclusion_reason(s[:-4], bases, months[s][0]) or (None if stamps else "no_files")
        if reason:
            excluded[s] = reason
        else:
            kept.append(s)
    say(f"kept {len(kept)}, left out {len(excluded)}")
    return {"listed_at": datetime.now(UTC).isoformat(timespec="seconds"), "n_pairs": len(pairs), "kept": kept,
            "excluded": excluded, "months": months}
