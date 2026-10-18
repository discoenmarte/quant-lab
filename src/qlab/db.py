"""DuckDB access for the ledger, audit log, data catalog and gauntlet results.

Connections are short-lived: open, do the work, close. DuckDB lets one process write at a time, so when
another process (usually the API server) holds the lock, ``connect`` retries for about ten seconds before
giving up with a clear message.
"""

from __future__ import annotations

import contextlib
import json
import math
import time
from collections.abc import Iterator
from typing import Any

import duckdb

from . import config

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS trials (
        trial_id VARCHAR PRIMARY KEY,
        created_at TIMESTAMP,
        question VARCHAR,
        family VARCHAR,
        strategy VARCHAR,
        params VARCHAR,
        symbol VARCHAR,
        start_date DATE,
        end_date DATE,
        source VARCHAR,
        sharpe DOUBLE,
        cagr DOUBLE,
        max_dd DOUBLE,
        n_obs INTEGER,
        metrics VARCHAR,
        data_hash VARCHAR
    )""",
    "CREATE SEQUENCE IF NOT EXISTS audit_seq",
    """CREATE TABLE IF NOT EXISTS audit (
        id BIGINT DEFAULT nextval('audit_seq'),
        ts TIMESTAMP,
        actor VARCHAR,
        action VARCHAR,
        target VARCHAR,
        reason VARCHAR,
        details VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS sources (
        name VARCHAR PRIMARY KEY,
        kind VARCHAR,
        description VARCHAR,
        last_attempt TIMESTAMP,
        last_success TIMESTAMP,
        status VARCHAR,
        message VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS series (
        source VARCHAR,
        symbol VARCHAR,
        interval VARCHAR,
        first_ts TIMESTAMP,
        last_ts TIMESTAMP,
        n_rows BIGINT,
        path VARCHAR,
        updated_at TIMESTAMP,
        PRIMARY KEY (source, symbol, interval)
    )""",
    """CREATE TABLE IF NOT EXISTS quality_issues (
        source VARCHAR,
        symbol VARCHAR,
        interval VARCHAR,
        ts TIMESTAMP,
        check_name VARCHAR,
        severity VARCHAR,
        detail VARCHAR,
        found_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS gauntlet_cards (
        card_id VARCHAR PRIMARY KEY,
        created_at TIMESTAMP,
        question VARCHAR,
        family VARCHAR,
        strategy VARCHAR,
        params VARCHAR,
        symbol VARCHAR,
        verdict VARCHAR,
        stages VARCHAR,
        thresholds_version INTEGER,
        report_path VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS thresholds (
        version INTEGER PRIMARY KEY,
        created_at TIMESTAMP,
        reason VARCHAR,
        config VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS lockbox_uses (
        family VARCHAR,
        question VARCHAR,
        card_id VARCHAR,
        used_at TIMESTAMP
    )""",
    # M7a: the forward paper book. Entries are report cards being tracked; decisions are append-only and each
    # is stored before the bar it will be scored on exists; scores keep one row per scoring that saw new bars.
    """CREATE TABLE IF NOT EXISTS paper_entries (
        entry_id VARCHAR PRIMARY KEY,
        card_id VARCHAR,
        kind VARCHAR,
        question VARCHAR,
        family VARCHAR,
        strategy VARCHAR,
        scope VARCHAR,
        params VARCHAR,
        verdict VARCHAR,
        added_at TIMESTAMP,
        status VARCHAR,
        note VARCHAR,
        reference VARCHAR,
        retired_at TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS paper_decisions (
        entry_id VARCHAR,
        as_of DATE,
        decided_at TIMESTAMP,
        targets VARCHAR,
        next_open TIMESTAMP,
        inputs_hash VARCHAR,
        code_version VARCHAR,
        PRIMARY KEY (entry_id, as_of)
    )""",
    """CREATE TABLE IF NOT EXISTS paper_scores (
        entry_id VARCHAR,
        scored_at TIMESTAMP,
        through DATE,
        n_days INTEGER,
        summary VARCHAR,
        trial_id VARCHAR
    )""",
    # M6: the owner's registry actions (append-only). Lifecycle states are derived, not stored (see registry.py).
    """CREATE TABLE IF NOT EXISTS registry_events (
        card_id VARCHAR,
        ts TIMESTAMP,
        action VARCHAR,
        reason VARCHAR,
        actor VARCHAR
    )""",
]


MIGRATIONS = [
    # M1: series carry their kind (bars or factors) and a content hash.
    "ALTER TABLE series ADD COLUMN IF NOT EXISTS kind VARCHAR",
    "ALTER TABLE series ADD COLUMN IF NOT EXISTS data_hash VARCHAR",
    # M3: the Sharpe of the return beyond static benchmark exposure, which the gauntlet's statistics use.
    "ALTER TABLE trials ADD COLUMN IF NOT EXISTS edge_sharpe DOUBLE",
    "ALTER TABLE lockbox_uses ADD COLUMN IF NOT EXISTS reason VARCHAR",
]


def _init(con: duckdb.DuckDBPyConnection) -> None:
    """Create missing tables and apply additive migrations. Safe to run on every connection.

    Args:
        con: Open read-write connection.
    """
    for stmt in SCHEMA + MIGRATIONS:
        con.execute(stmt)


@contextlib.contextmanager
def connect() -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the lab database, make sure the schema exists, and close it afterwards.

    Returns:
        A context manager yielding a read-write DuckDB connection.

    Raises:
        RuntimeError: Another process kept the write lock for more than ~10 seconds.
    """
    config.ensure_dirs()
    last_err: Exception | None = None
    con = None
    for _ in range(40):
        try:
            con = duckdb.connect(str(config.db_path()))
            break
        except duckdb.IOException as err:
            last_err = err
            time.sleep(0.25)
    if con is None:
        raise RuntimeError(
            f"Could not open {config.db_path()}: another lab process holds the lock. "
            "Stop it, or run the action through the API."
        ) from last_err
    try:
        _init(con)
        yield con
    finally:
        con.close()


def rows(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Run a query and return its rows as JSON-friendly dicts.

    Args:
        sql: SQL with ``?`` placeholders.
        params: Values for the placeholders.

    Returns:
        One dict per row. Dates and timestamps are ISO-8601 strings.
    """
    with connect() as con:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        out = []
        for rec in cur.fetchall():
            item = {}
            for k, v in zip(cols, rec, strict=True):
                item[k] = v.isoformat() if hasattr(v, "isoformat") else v
            out.append(item)
        return out


def scalar(sql: str, params: list[Any] | None = None) -> Any:
    """Run a query and return the first column of the first row.

    Args:
        sql: SQL with ``?`` placeholders.
        params: Values for the placeholders.

    Returns:
        The value, or None when the query returns no rows.
    """
    with connect() as con:
        res = con.execute(sql, params or []).fetchone()
        return res[0] if res else None


def dumps(obj: Any) -> str:
    """Serialise a value to JSON for storage in a VARCHAR column.

    Args:
        obj: Any JSON-like value. Non-JSON types (dates, numpy scalars) are converted with ``str``.

    Returns:
        A JSON string with sorted keys, so equal inputs give equal strings.
    """
    return json.dumps(obj, default=str, sort_keys=True)


def json_safe(obj: Any) -> Any:
    """Make a value strict-JSON safe: NaN and infinity become None, numpy scalars become Python numbers.

    Browsers reject NaN in JSON, and missing values are common in reports (no PBO without parameters).

    Args:
        obj: Nested dicts, lists, tuples and scalars.

    Returns:
        The same structure with only JSON-native values.
    """
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item) and getattr(obj, "shape", None) == ():
        obj = obj.item()  # numpy scalar
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj
