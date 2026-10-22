"""Radar storage: point-in-time Parquet series, their catalog, the source status, and a polite cached fetcher.

Design (decision 0016):

- **Every row says when the world could know it.** A series row has ``ts`` (start of the period it describes, UTC),
  its values, ``published_at`` (when the source made that value public: the period's end plus the source's
  publication lag, or the fetch time for a period still running), ``fetched_at`` (when the radar got it) and
  ``partial`` (the period hadn't ended when fetched, so the value will still change). Research rules read
  ``published_at``, never ``ts``.
- **One file per series,** ``<data>/curated/radar/<source>/<name>.parquet``. A new fetch replaces rows for the same
  ``ts``: sources revise recent values (a running day's article count, a story's points), and the newest fetch is
  the best knowledge. The catalog row in ``radar_series`` records the extent and the last update.
- **Downloads are cached** under ``<data>/raw/radar/<source>``: finished history forever, live answers for a short
  time, so a rerun doesn't ask a source twice and a failed parse can be replayed.
- **Polite by construction.** One shared client with the lab's user agent (it names the project and its address, as
  Wikimedia asks), a minimum gap between requests per host (GDELT asks for one request every five seconds and
  refuses more), retries with growing waits on rate limits and server errors, and no retries on other refusals.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pandas as pd

from .. import config, db
from ..data.http import USER_AGENT, SourceError
from ..timeutil import naive_utc

BASE_COLUMNS = ["ts", "published_at", "fetched_at", "partial"]
# Seconds between two requests to the same host. GDELT's DOC API refuses more than one request every five seconds.
MIN_GAP = {"api.gdeltproject.org": 6.0, "hn.algolia.com": 0.3, "wikimedia.org": 0.2}
DEFAULT_GAP = 0.25
# Waits before each retry after a rate limit or a server error; GDELT's limit window is longer than its stated gap.
BACKOFF = {"api.gdeltproject.org": (45.0, 120.0)}
DEFAULT_BACKOFF = (2.0, 6.0)


def series_path(source: str, name: str) -> Path:
    """Where a radar series is stored.

    Args:
        source: Source name, e.g. ``wiki``.
        name: Series name within the source, already a slug.

    Returns:
        ``<data>/curated/radar/<source>/<name>.parquet``.
    """
    return config.curated_dir() / "radar" / source / f"{name}.parquet"


def raw_path(source: str, name: str) -> Path:
    """Where a raw download is cached.

    Args:
        source: Source name.
        name: File name (a slug plus an extension).

    Returns:
        ``<data>/raw/radar/<source>/<name>``.
    """
    return config.raw_dir() / "radar" / source / name


def slug(text: str, limit: int = 80) -> str:
    """A safe file and key name for a query, an article or a market.

    Args:
        text: Any text.
        limit: Maximum length.

    Returns:
        Lowercase letters, digits, ``_`` and ``-`` only; never empty.
    """
    s = re.sub(r"[^a-z0-9_-]+", "_", text.lower()).strip("_")
    return (s or "x")[:limit]


def _ts(values: Any) -> pd.Series:
    """Timestamps as tz-aware UTC.

    Args:
        values: Anything ``pd.to_datetime`` reads; naive values are read as UTC.

    Returns:
        A datetime series in UTC.
    """
    return pd.to_datetime(pd.Series(values), utc=True)


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """A series frame in canonical form: the base columns typed, rows sorted by ``ts``, one row per ``ts``.

    Args:
        df: Rows with at least ``ts``, ``published_at`` and ``fetched_at``; ``partial`` defaults to False.

    Returns:
        A new frame; for duplicate ``ts`` the row fetched last wins.

    Raises:
        ValueError: A base column is missing.
    """
    out = df.copy()
    if "partial" not in out:
        out["partial"] = False
    missing = [c for c in BASE_COLUMNS if c not in out]
    if missing:
        raise ValueError(f"radar rows are missing {missing}")
    for c in ("ts", "published_at", "fetched_at"):
        out[c] = _ts(out[c]).to_numpy()
    out["partial"] = out["partial"].fillna(False).astype(bool)
    out = out.sort_values(["ts", "fetched_at"]).drop_duplicates("ts", keep="last")
    return out.reset_index(drop=True)


def read(source: str, name: str) -> pd.DataFrame:
    """A stored radar series.

    Args:
        source: Source name.
        name: Series name.

    Returns:
        The rows, ``ts`` ascending, times as tz-aware UTC.

    Raises:
        FileNotFoundError: Not collected yet.
    """
    path = series_path(source, name)
    if not path.exists():
        raise FileNotFoundError(f"radar series {source}/{name} not collected yet: run `qlab radar collect`")
    df = pd.read_parquet(path)
    for c in ("ts", "published_at", "fetched_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def upsert(source: str, name: str, df: pd.DataFrame, meta: dict[str, Any]) -> dict[str, Any]:
    """Merge new rows into a stored series and update its catalog row.

    Args:
        source: Source name.
        name: Series name (a slug).
        df: New rows (see ``normalise``).
        meta: ``label``, ``category``, ``unit`` and ``frequency`` for the catalog.

    Returns:
        The catalog row: ``key, source, name, label, category, unit, frequency, first_ts, last_ts, n_rows,
        last_published, updated_at, path`` and ``added`` (rows that weren't stored before).
    """
    new = normalise(df)
    path = series_path(source, name)
    old = read(source, name) if path.exists() else None
    before = set() if old is None else set(old["ts"])
    full = new if old is None else normalise(pd.concat([old, new], ignore_index=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".partial.parquet")
    full.to_parquet(tmp, index=False)
    tmp.replace(path)
    key = f"{source}/{name}"
    row = {
        "key": key, "source": source, "name": name, "label": meta.get("label", name),
        "category": meta.get("category", ""), "unit": meta.get("unit", ""), "frequency": meta.get("frequency", "1d"),
        "first_ts": naive_utc(full["ts"].min().to_pydatetime()) if len(full) else None,
        "last_ts": naive_utc(full["ts"].max().to_pydatetime()) if len(full) else None,
        "n_rows": int(len(full)),
        "last_published": naive_utc(full["published_at"].max().to_pydatetime()) if len(full) else None,
        "updated_at": naive_utc(), "path": str(path),
    }
    with db.connect() as con:
        con.execute("""INSERT OR REPLACE INTO radar_series (key, source, name, label, category, unit, frequency,
                       first_ts, last_ts, n_rows, last_published, updated_at, path)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [row[k] for k in (
            "key", "source", "name", "label", "category", "unit", "frequency", "first_ts", "last_ts", "n_rows",
            "last_published", "updated_at", "path")])
    return {**row, "added": int(sum(t not in before for t in new["ts"]))}


def catalog(source: str | None = None) -> list[dict[str, Any]]:
    """Every stored radar series.

    Args:
        source: Only this source; None means all.

    Returns:
        Catalog rows ordered by source and name, times as ISO strings.
    """
    where, args = ("WHERE source = ?", [source]) if source else ("", [])
    return db.rows(f"SELECT * FROM radar_series {where} ORDER BY source, name", args)


def mark_source(source: str, ok: bool, message: str, now: datetime | None = None) -> None:
    """Record the outcome of a source's latest collection.

    Args:
        source: Source name.
        ok: Whether it succeeded (a partial success counts as ok, with the problems in ``message``).
        message: What happened, in plain words.
        now: Time (None means now).
    """
    when = naive_utc(now)
    with db.connect() as con:
        prev = con.execute("SELECT last_success FROM radar_sources WHERE source = ?", [source]).fetchone()
        last_ok = when if ok else (prev[0] if prev else None)
        con.execute("""INSERT OR REPLACE INTO radar_sources (source, last_attempt, last_success, status, message)
                       VALUES (?, ?, ?, ?, ?)""", [source, when, last_ok, "ok" if ok else "error", message[:500]])


HEADLINE_COLUMNS = ["source", "item_id", "topic", "title", "url", "domain", "language", "published_at", "fetched_at",
                    "points", "pos", "neg", "score"]


def add_headlines(rows: list[dict[str, Any]]) -> int:
    """Store headlines, keeping the first copy of each item and updating only its points.

    Headlines are untrusted text from the web: they are stored and shown as data, never interpreted or followed.

    Args:
        rows: Dicts with ``HEADLINE_COLUMNS`` (``points``, ``pos``, ``neg`` and ``score`` may be None).

    Returns:
        Rows given.
    """
    if not rows:
        return 0
    with db.connect() as con:
        for r in rows:
            vals = [naive_utc(pd.Timestamp(r[c]).to_pydatetime()) if c in ("published_at", "fetched_at")
                    else r.get(c) for c in HEADLINE_COLUMNS]
            con.execute(f"""INSERT INTO radar_headlines ({', '.join(HEADLINE_COLUMNS)})
                            VALUES ({', '.join('?' * len(HEADLINE_COLUMNS))})
                            ON CONFLICT (source, item_id) DO UPDATE SET points = excluded.points""", vals)
    return len(rows)


def headlines(limit: int = 100, topic: str | None = None, since: datetime | None = None) -> list[dict[str, Any]]:
    """Stored headlines, newest first.

    Args:
        limit: Maximum rows.
        topic: Only this topic (a GDELT query id or an HN keyword); None means all.
        since: Only those published after this time (naive UTC); None means all.

    Returns:
        Rows with ``HEADLINE_COLUMNS``, times as ISO strings.
    """
    where, args = [], []
    if topic:
        where.append("topic = ?")
        args.append(topic)
    if since is not None:
        where.append("published_at > ?")
        args.append(naive_utc(since))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return db.rows(f"SELECT * FROM radar_headlines {clause} ORDER BY published_at DESC LIMIT ?", [*args, limit])


def sources() -> list[dict[str, Any]]:
    """The status of every radar source.

    Returns:
        Rows ``source, last_attempt, last_success, status, message``.
    """
    return db.rows("SELECT * FROM radar_sources ORDER BY source")


class Fetcher:
    """Polite HTTP for the radar: one client, a gap per host, retries with backoff and a raw-download cache.

    Args:
        client: HTTP client (tests pass one with a mock transport); None opens one with the lab's user agent.
        sleep: Function that waits (tests pass a no-op).
        clock: Monotonic clock in seconds.
    """

    def __init__(self, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60)
        self.sleep, self.clock = sleep, clock
        self._last: dict[str, float] = {}

    def _wait_turn(self, host: str) -> None:
        """Sleep until the host's minimum gap since the last request has passed.

        Args:
            host: Host name.
        """
        gap = MIN_GAP.get(host, DEFAULT_GAP)
        last = self._last.get(host)
        if last is not None:
            wait = gap - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def get(self, url: str, params: dict[str, Any] | None = None, *, cache: Path | None = None,
            ttl: float | None = None, allow_404: bool = False, headers: dict[str, str] | None = None,
            retries: int | None = None) -> bytes | None:
        """Download a URL, or return the cached copy while it is fresh.

        Args:
            url: Address.
            params: Query parameters.
            cache: File to cache the body in; None doesn't cache.
            ttl: Seconds a cached body stays fresh; None keeps it forever (finished history).
            allow_404: Return None on 404 instead of raising.
            headers: Extra request headers.
            retries: Retries after a rate limit or server error; None uses the host's backoff list.

        Returns:
            The body, or None for an allowed 404.

        Raises:
            SourceError: A refusal that retrying can't fix, or every attempt failed. The message names the host and
                the status, never a secret.
        """
        if cache is not None and cache.exists() and cache.stat().st_size > 0:
            if ttl is None or time.time() - cache.stat().st_mtime < ttl:
                return cache.read_bytes()
        host = urlsplit(url).hostname or ""
        waits = BACKOFF.get(host, DEFAULT_BACKOFF)
        if retries is not None:
            waits = waits[:retries]
        last = ""
        for attempt in range(len(waits) + 1):
            self._wait_turn(host)
            try:
                res = self.client.get(url, params=params, headers=headers)
            except httpx.HTTPError as err:
                last = type(err).__name__
            else:
                if res.status_code == 404 and allow_404:
                    return None
                if res.status_code == 200:
                    if cache is not None:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        tmp = cache.with_name(cache.name + ".tmp")
                        tmp.write_bytes(res.content)
                        tmp.replace(cache)
                    return res.content
                last = f"HTTP {res.status_code}"
                if res.status_code != 429 and res.status_code < 500:
                    raise SourceError(f"{host}: {last} {res.text[:160].strip()}")
            if attempt < len(waits):
                self.sleep(waits[attempt])
        raise SourceError(f"{host}: giving up after {len(waits) + 1} tries ({last})")
