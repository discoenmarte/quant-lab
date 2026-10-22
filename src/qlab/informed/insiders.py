"""Aggregate insider trading from the SEC's Form 4 data sets, as a daily series the lab can time the market with.

Design (decision 0014):

- **Source.** The SEC's "Insider Transactions Data Sets": one zip per quarter since 2006 with every Form 3, 4 and 5
  filed on EDGAR, as tab-separated tables. Downloads are cached in ``<data>/raw/sec``; only quarters not cached are
  fetched. The SEC asks automated clients to name themselves with a contact address in the user agent; the lab sends
  ``QLAB_SEC_USER_AGENT`` (the owner sets it) and pauses between files.
- **What counts.** Original Form 4 filings (amendments are corrections and can arrive years later), non-derivative
  open-market purchases (code ``P``) and sales (code ``S``). Grants, option exercises, gifts and tax withholding are
  not trades on information. Each filing counts once per side, whatever its number of lines.
- **When it was known.** Rows are keyed by the EDGAR filing date, the day the trade became public. Filings accepted
  after the close still carry that day's date, so the strategy uses a day's filings only from the next session on.
- **The series** (``sec/INSIDERS/1d``): per filing date, filings with a purchase and with a sale, distinct companies
  with each, and the dollar value of each side.
"""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Callable
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from .. import audit, config
from ..data import store

URL = "https://www.sec.gov/files/structureddata/data/form-345-data-sets/{q}_form345.zip"
FIRST_QUARTER = (2006, 1)
SOURCE, SYMBOL, INTERVAL = "sec", "INSIDERS", "1d"
PAUSE_SECONDS = 0.6  # the SEC allows ten requests a second; one file every half second is far below
COLUMNS = ["ts", "buy_filings", "sell_filings", "buy_firms", "sell_firms", "buy_usd", "sell_usd"]


class SECError(RuntimeError):
    """The SEC refused a request or a file couldn't be read."""


def user_agent() -> str:
    """The user agent the SEC asks automated clients to send.

    Returns:
        ``QLAB_SEC_USER_AGENT``.

    Raises:
        SECError: It isn't set, or has no contact address in it.
    """
    ua = config.setting("QLAB_SEC_USER_AGENT") or ""
    if "@" not in ua:
        raise SECError("the SEC wants a contact address in the user agent: set QLAB_SEC_USER_AGENT, e.g. "
                       f"'quant-lab your-name you@example.com', in {config.env_file()}")
    return ua


def quarters(today: date, first: tuple[int, int] = FIRST_QUARTER) -> list[str]:
    """Every quarter from the first data set to the last finished quarter.

    Args:
        today: The current date.
        first: First ``(year, quarter)``.

    Returns:
        Names like ``2006q1``, oldest first. The running quarter is left out: its file appears after it ends.
    """
    y, q = first
    last = (today.year, (today.month - 1) // 3)  # the quarter before the running one
    if last[1] == 0:
        last = (today.year - 1, 4)
    out = []
    while (y, q) <= last:
        out.append(f"{y}q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def raw_path(quarter: str) -> Path:
    """Where a quarter's zip is cached.

    Args:
        quarter: e.g. ``2024q2``.

    Returns:
        ``<data>/raw/sec/<quarter>_form345.zip``.
    """
    return config.raw_dir() / "sec" / f"{quarter}_form345.zip"


def download(quarter: str, client: httpx.Client | None = None) -> Path | None:
    """Fetch one quarter's zip into the cache unless it is already there.

    Args:
        quarter: e.g. ``2024q2``.
        client: HTTP client (tests pass a mock); None opens one.

    Returns:
        The cached file, or None when the SEC has no file for the quarter (404).

    Raises:
        SECError: The SEC refused the request (403 usually means a missing contact address or a rate block).
    """
    path = raw_path(quarter)
    if path.exists() and path.stat().st_size > 0:
        return path
    http = client or httpx.Client(timeout=120, follow_redirects=True)
    res = http.get(URL.format(q=quarter), headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip, deflate"})
    time.sleep(PAUSE_SECONDS)
    if res.status_code == 404:
        return None
    if res.status_code != 200 or not res.content.startswith(b"PK"):
        raise SECError(f"SEC {quarter}: HTTP {res.status_code}"
                       + (" (rate block or missing contact address)" if res.status_code == 403 else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial")
    tmp.write_bytes(res.content)
    tmp.rename(path)
    return path


def _table(z: zipfile.ZipFile, name: str, cols: list[str]) -> pd.DataFrame:
    """Read some columns of one table in a quarter's zip.

    Args:
        z: The open zip.
        name: Table name, e.g. ``SUBMISSION``.
        cols: Columns to keep.

    Returns:
        The table as strings.

    Raises:
        SECError: The table is missing.
    """
    member = next((n for n in z.namelist() if n.upper().endswith(f"{name}.TSV")), None)
    if member is None:
        raise SECError(f"{name}.tsv is missing from the zip")
    with z.open(member) as fh:
        return pd.read_csv(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"), sep="\t", usecols=cols,
                           dtype=str, quoting=3, on_bad_lines="skip")


def parse_quarter(payload: bytes | Path) -> pd.DataFrame:
    """Daily insider buying and selling from one quarter's zip.

    Args:
        payload: The zip's bytes or path.

    Returns:
        One row per filing date with ``COLUMNS``.

    Raises:
        SECError: A table is missing.
    """
    src = io.BytesIO(payload) if isinstance(payload, bytes) else payload
    with zipfile.ZipFile(src) as z:
        sub = _table(z, "SUBMISSION", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"])
        trans = _table(z, "NONDERIV_TRANS", ["ACCESSION_NUMBER", "TRANS_CODE", "TRANS_SHARES",
                                             "TRANS_PRICEPERSHARE"])
    sub = sub[sub["DOCUMENT_TYPE"].str.strip() == "4"]
    trans = trans[trans["TRANS_CODE"].str.strip().isin(["P", "S"])].copy()
    trans["usd"] = (pd.to_numeric(trans["TRANS_SHARES"], errors="coerce").fillna(0.0)
                    * pd.to_numeric(trans["TRANS_PRICEPERSHARE"], errors="coerce").fillna(0.0))
    rows = trans.merge(sub, on="ACCESSION_NUMBER", how="inner")
    if rows.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows["ts"] = pd.to_datetime(rows["FILING_DATE"], format="%d-%b-%Y", errors="coerce", utc=True)
    rows = rows.dropna(subset=["ts"])
    side = rows["TRANS_CODE"].str.strip().map({"P": "buy", "S": "sell"})
    out = {}
    for name, part in rows.groupby(side):
        g = part.groupby("ts")
        out[f"{name}_filings"] = g["ACCESSION_NUMBER"].nunique()
        out[f"{name}_firms"] = g["ISSUERCIK"].nunique()
        out[f"{name}_usd"] = g["usd"].sum()
    df = pd.DataFrame(out).fillna(0.0).reset_index().rename(columns={"index": "ts"})
    for c in COLUMNS[1:]:
        if c not in df:
            df[c] = 0.0
    return df[COLUMNS].sort_values("ts").reset_index(drop=True)


def build(log: Callable[[str], None] | None = None, today: date | None = None,
          client: httpx.Client | None = None, actor: str = "user") -> dict[str, Any]:
    """Download what is missing, parse every quarter and store the daily series.

    Args:
        log: Receives one line per quarter.
        today: The current date (None means today).
        client: HTTP client (tests pass a mock).
        actor: Who ran it, for the audit log.

    Returns:
        The catalog entry of ``sec/INSIDERS/1d`` plus ``quarters`` (parsed), ``missing`` (no file at the SEC yet).

    Raises:
        SECError: The SEC refused a download.
    """
    say = log or (lambda _m: None)
    parts, missing = [], []
    for q in quarters(today or date.today()):
        path = download(q, client)
        if path is None:
            missing.append(q)
            say(f"{q}: not published yet")
            continue
        df = parse_quarter(path)
        say(f"{q}: {len(df)} filing days, {int(df['buy_filings'].sum())} purchases, "
            f"{int(df['sell_filings'].sum())} sales")
        parts.append(df)
    if not parts:
        raise SECError("no quarter could be read")
    full = (pd.concat(parts, ignore_index=True).groupby("ts", as_index=False).sum().sort_values("ts")
            .reset_index(drop=True))
    entry = store.write_series(SOURCE, SYMBOL, INTERVAL, full, kind="signals")
    load_series.cache_clear()
    audit.log("data.ingest", target=SOURCE, actor=actor,
              details={"quarters": len(parts), "missing": missing, "rows": len(full)})
    return {**entry, "quarters": len(parts), "missing": missing}


@lru_cache(maxsize=4)
def load_series(hash_key: str = "") -> pd.DataFrame:
    """The stored daily series, indexed by filing date (naive UTC midnight).

    Args:
        hash_key: Cache key (the series' path and modification time); callers use ``series()``.

    Returns:
        The counts and values per filing date.
    """
    df = store.read_series(SOURCE, SYMBOL, INTERVAL)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None).dt.normalize()
    return df.set_index("ts").sort_index()


def series() -> pd.DataFrame:
    """The stored daily series, cached until the file changes.

    Returns:
        See ``load_series``.

    Raises:
        FileNotFoundError: It hasn't been built (``qlab informed ingest``).
    """
    path = store.series_path(SOURCE, SYMBOL, INTERVAL)
    if not path.exists():
        raise FileNotFoundError("no insider series yet: run `qlab informed ingest`")
    return load_series(f"{path}:{path.stat().st_mtime_ns}")


JUDGE_FROM = "2009-01-02"  # the data starts in 2006 and the rule needs up to three years of history to normalise


def sentiment(window: int, lookback: int, frame: pd.DataFrame | None = None) -> pd.Series:
    """Insider sentiment by calendar day: the net purchase ratio over a window, against its own past.

    The net purchase ratio is (purchases - sales) / (purchases + sales), counted in filings over the last ``window``
    calendar days (Lakonishok and Lee, 2001). It is standardised against its mean and spread over the previous
    ``lookback`` days, so a reading says whether insiders are buying more than they usually do. A filing dated d is
    counted from d + 1 on: it may have been accepted after that day's close.

    Args:
        window: Days of filings per reading.
        lookback: Days of past readings to standardise against.
        frame: The daily series (tests pass one); None loads the stored series.

    Returns:
        Z-scores by calendar date (naive midnight), NaN until enough history exists.
    """
    s = frame if frame is not None else series()
    daily = s[["buy_filings", "sell_filings"]].astype(float).asfreq("D", fill_value=0.0)
    daily.index = daily.index + pd.Timedelta(days=1)  # known from the next day on
    roll = daily.rolling(f"{int(window)}D").sum()
    total = roll["buy_filings"] + roll["sell_filings"]
    npr = ((roll["buy_filings"] - roll["sell_filings"]) / total).where(total > 0)
    past = npr.rolling(f"{int(lookback)}D", min_periods=max(30, int(lookback) // 2))
    z = (npr - past.mean()) / past.std()
    z.iloc[: int(window)] = float("nan")  # the first window is incomplete
    return z


def insider_timing(bars: pd.DataFrame, window: int = 60, lookback: int = 730, threshold: float = 0.0) -> pd.Series:
    """Hold the index while insiders across the market buy more than usual, cash otherwise.

    Args:
        bars: Normalised daily bars of the instrument traded (an index ETF).
        window: Days of filings per reading.
        lookback: Days of history to standardise the reading against.
        threshold: Hold when the standardised reading is above this many standard deviations.

    Returns:
        1.0 while invested, 0.0 in cash, 0.0 before the reading exists.
    """
    z = sentiment(window, lookback)
    days = pd.DatetimeIndex(pd.to_datetime(bars["ts"], utc=True)).tz_localize(None).normalize()
    at = z.reindex(z.index.union(days)).ffill().reindex(days)
    return pd.Series(((at > threshold) & at.notna()).astype(float).to_numpy(), index=bars.index)
