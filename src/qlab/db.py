"""DuckDB access. One file holds the ledger, audit log, data catalog and gauntlet results.

Connections are short-lived: open, do the work, close. DuckDB allows one writing process at a time, so if
another process (for example the API server) holds the lock we retry briefly before giving up.
"""

from __future__ import annotations

import contextlib
import json
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
]


def _init(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in SCHEMA:
        con.execute(stmt)


@contextlib.contextmanager
def connect() -> Iterator[duckdb.DuckDBPyConnection]:
    config.ensure_dirs()
    last_err: Exception | None = None
    con = None
    for _ in range(40):
        try:
            con = duckdb.connect(str(config.db_path()))
            break
        except duckdb.IOException as err:  # another process holds the write lock
            last_err = err
            time.sleep(0.25)
    if con is None:
        raise RuntimeError(
            f"Could not open {config.db_path()} (locked by another process?). "
            "Stop the other lab process or run the action through the API."
        ) from last_err
    try:
        _init(con)
        yield con
    finally:
        con.close()


def rows(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Run a query and return a list of dicts (JSON-friendly)."""
    with connect() as con:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        out = []
        for rec in cur.fetchall():
            item = {}
            for k, v in zip(cols, rec, strict=True):
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                item[k] = v
            out.append(item)
        return out


def scalar(sql: str, params: list[Any] | None = None) -> Any:
    with connect() as con:
        res = con.execute(sql, params or []).fetchone()
        return res[0] if res else None


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, sort_keys=True)
