"""Ingestion: download a source, store each series, check its quality and update the catalog.

Re-running is safe: archive files are cached, series files are rewritten, and quality issues are replaced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import audit, db
from ..timeutil import naive_utc
from . import quality, store
from .sources import binance, french, yahoo
from .universe import CRYPTO, ETFS


@dataclass(frozen=True)
class SourceInfo:
    """What a source provides.

    Attributes:
        kind: ``"bars"`` or ``"factors"``.
        asset_class: ``"crypto"``, ``"equity"`` or ``"factors"``; selects quality limits.
        description: One line for the UI.
        symbols: Default symbols to ingest.
        intervals: Intervals to ingest per symbol.
    """

    kind: str
    asset_class: str
    description: str
    symbols: list[str] = field(default_factory=list)
    intervals: list[str] = field(default_factory=lambda: ["1d"])


SOURCES: dict[str, SourceInfo] = {
    "binance": SourceInfo(
        "bars", "crypto", "Binance spot klines from the public archive (checksummed)", list(CRYPTO), ["1d", "1h"]
    ),
    "yahoo": SourceInfo("bars", "equity", "Yahoo Finance daily ETF bars with adjusted close and events", list(ETFS)),
    "french": SourceInfo("factors", "factors", "Ken French daily factors and the risk-free rate", ["FF3"]),
}


def ensure_sources() -> None:
    """Create a catalog row for every known source that doesn't have one yet."""
    with db.connect() as con:
        for name, info in SOURCES.items():
            con.execute(
                """INSERT INTO sources (name, kind, description, status, message)
                   VALUES (?, ?, ?, 'never', 'not ingested yet') ON CONFLICT DO NOTHING""",
                [name, info.kind, info.description],
            )


def _set_source(name: str, **fields: Any) -> None:
    """Update columns of one source's catalog row.

    Args:
        name: Source name.
        **fields: Column values to set.
    """
    cols = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as con:
        con.execute(f"UPDATE sources SET {cols} WHERE name = ?", [*fields.values(), name])


def ingest(
    source: str,
    symbols: list[str] | None = None,
    log: Callable[[str], None] | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    """Ingest one source.

    Args:
        source: Key of ``SOURCES``.
        symbols: Restrict to these symbols. None ingests the source's default universe.
        log: Receives one progress line per series.
        actor: Who triggered it, for the audit log.

    Returns:
        ``{"source", "status", "series": [catalog entries with issue counts], "errors": [messages]}``.
        Status is ``ok``, ``warn`` (some series failed or have errors) or ``error`` (nothing stored).

    Raises:
        KeyError: Unknown source.
    """
    info = SOURCES[source]
    say = log or (lambda _msg: None)
    ensure_sources()
    _set_source(source, last_attempt=naive_utc(), status="running", message="ingesting")

    stored: list[dict[str, Any]] = []
    errors: list[str] = []
    for symbol in symbols or info.symbols:
        for interval in info.intervals:
            try:
                if source == "binance":
                    df = binance.fetch(symbol, interval)
                elif source == "yahoo":
                    df, events = yahoo.fetch(symbol)
                    store.write_events(source, symbol, events)
                else:
                    df = french.fetch()
                entry = store.write_series(source, symbol, interval, df, kind=info.kind)
                if info.kind == "bars":
                    issues = quality.check_bars(df, asset_class=info.asset_class, interval=interval)
                else:
                    issues = quality.check_factors(df)
                quality.record_issues(source, symbol, interval, issues)
                entry["n_errors"] = sum(i.severity == "error" for i in issues)
                entry["n_warnings"] = sum(i.severity == "warn" for i in issues)
                stored.append(entry)
                say(f"{source} {symbol} {interval}: {entry['n_rows']} rows, "
                    f"{entry['n_errors']} errors, {entry['n_warnings']} warnings")
            except Exception as err:  # one bad symbol must not stop the rest
                errors.append(f"{symbol} {interval}: {err}")
                say(f"{source} {symbol} {interval}: FAILED {err}")

    if not stored:
        status = "error"
    elif errors or any(e["n_errors"] for e in stored):
        status = "warn"
    else:
        status = "ok"
    message = f"{len(stored)} series stored" + (f", {len(errors)} failed" if errors else "")
    fields: dict[str, Any] = {"status": status, "message": message}
    if stored:
        fields["last_success"] = naive_utc()
    _set_source(source, **fields)
    audit.log("data.ingest", target=source, actor=actor,
              details={"status": status, "series": len(stored), "errors": errors[:5]})
    return {"source": source, "status": status, "series": stored, "errors": errors}


def list_sources() -> list[dict[str, Any]]:
    """Every source with its status and series counts.

    Returns:
        Source catalog rows plus ``n_series`` and ``universe`` (default symbols).
    """
    ensure_sources()
    rows = db.rows(
        """SELECT s.*, (SELECT count(*) FROM series x WHERE x.source = s.name) AS n_series
           FROM sources s ORDER BY name"""
    )
    for r in rows:
        info = SOURCES.get(r["name"])
        r["universe"] = info.symbols if info else []
        r["intervals"] = info.intervals if info else []
    return rows
