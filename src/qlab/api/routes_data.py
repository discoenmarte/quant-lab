"""Data endpoints: sources, series, coverage, quality issues, bars, and background ingestion jobs."""

from __future__ import annotations

import threading
import uuid
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..data import ingest, store
from ..data.bars import adjusted as adjust_bars
from ..timeutil import now_utc

router = APIRouter(prefix="/data")

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class IngestRequest(BaseModel):
    """Body of ``POST /api/data/ingest``.

    Attributes:
        source: Source to ingest; None ingests every source.
        symbols: Restrict to these symbols; None uses each source's default universe.
    """

    source: str | None = None
    symbols: list[str] | None = None


def _run_job(job_id: str, sources: list[str], symbols: list[str] | None) -> None:
    """Worker thread body: ingest each source and record progress on the job.

    Args:
        job_id: Key into ``_jobs``.
        sources: Source names, in order.
        symbols: Optional symbol filter passed to every source.
    """
    job = _jobs[job_id]

    def log(msg: str) -> None:
        job["log"].append(f"{now_utc().strftime('%H:%M:%S')} {msg}")

    try:
        for name in sources:
            res = ingest.ingest(name, symbols, log=log)
            job["results"].append(
                {
                    "source": name,
                    "status": res["status"],
                    "n_series": len(res["series"]),
                    "errors": res["errors"],
                }
            )
        job["status"] = "done"
    except Exception as err:  # surfaced in the UI instead of dying silently in the thread
        job["status"] = "failed"
        log(f"job failed: {err}")
    finally:
        job["finished_at"] = now_utc().isoformat(timespec="seconds")


@router.post("/ingest")
def start_ingest(req: IngestRequest) -> dict[str, Any]:
    """Start ingesting in the background.

    Args:
        req: Which source and symbols to ingest.

    Returns:
        The new job: ``id, status, sources, started_at, log, results``.

    Raises:
        HTTPException: 404 for an unknown source, 409 while another ingest is running.
    """
    if req.source and req.source not in ingest.SOURCES:
        raise HTTPException(status_code=404, detail=f"unknown source {req.source}")
    with _lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            raise HTTPException(status_code=409, detail="an ingest is already running")
        job_id = uuid.uuid4().hex[:8]
        sources = [req.source] if req.source else list(ingest.SOURCES)
        _jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "sources": sources,
            "started_at": now_utc().isoformat(timespec="seconds"),
            "finished_at": None,
            "log": [],
            "results": [],
        }
    threading.Thread(target=_run_job, args=(job_id, sources, req.symbols), daemon=True).start()
    return _jobs[job_id]


@router.get("/jobs")
def jobs() -> list[dict[str, Any]]:
    """Recent ingestion jobs, newest first.

    Returns:
        Up to 20 job dicts.
    """
    return sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)[:20]


@router.get("/sources")
def sources() -> list[dict[str, Any]]:
    """Every data source with status, freshness and series count.

    Returns:
        Source catalog rows.
    """
    return ingest.list_sources()


@router.get("/series")
def series() -> list[dict[str, Any]]:
    """Every stored series with its quality-issue counts.

    Returns:
        Series catalog rows.
    """
    return store.list_series()


@router.get("/coverage")
def coverage(interval: str = "1d") -> list[dict[str, Any]]:
    """Rows per symbol and year for the coverage heatmap.

    Args:
        interval: Interval to summarise.

    Returns:
        Records ``{"source", "symbol", "year", "n_rows"}``.
    """
    return store.coverage(interval)


@router.get("/issues")
def issues(limit: int = 500, source: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
    """Open quality issues, errors first.

    Args:
        limit: Maximum number of issues.
        source: Only this source.
        symbol: Only this symbol.

    Returns:
        Issue rows.
    """
    where, params = [], []
    if source:
        where.append("source = ?")
        params.append(source)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    return db.rows(
        f"""SELECT * FROM quality_issues {clause}
            ORDER BY severity = 'error' DESC, source, symbol, ts NULLS LAST LIMIT ?""",
        [*params, limit],
    )


@router.get("/bars")
def bars(
    source: str,
    symbol: str,
    interval: str = "1d",
    adjusted: bool = False,
    limit: int = 1500,
) -> dict[str, Any]:
    """Recent bars and corporate events for the candle inspector.

    Args:
        source: Source name.
        symbol: Instrument.
        interval: Bar interval.
        adjusted: Scale OHLC by the adjusted close (sources that provide it).
        limit: Number of most recent bars to return.

    Returns:
        ``bars`` (time as Unix seconds, OHLCV), ``events`` within that window, ``n_total`` and whether the
        prices are ``adjusted``.

    Raises:
        HTTPException: 404 when the series hasn't been ingested.
    """
    try:
        df = store.read_series(source, symbol, interval)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    has_adj = "adj_close" in df.columns
    view = adjust_bars(df) if adjusted and has_adj else df
    view = view.tail(limit)
    start = view["ts"].min() if len(view) else pd.Timestamp.min.tz_localize("UTC")
    ev = store.read_events(source, symbol)
    ev = ev[ev["ts"] >= start] if len(ev) else ev
    return {
        "bars": [
            {
                "time": int(r.ts.timestamp()),
                "open": float(r.open),
                "high": float(r.high),
                "low": float(r.low),
                "close": float(r.close),
                "volume": float(r.volume) if pd.notna(r.volume) else 0.0,
            }
            for r in view.itertuples()
        ],
        "events": [
            {"time": int(r.ts.timestamp()), "kind": r.kind, "value": float(r.value)} for r in ev.itertuples()
        ],
        "n_total": int(len(df)),
        "adjusted": bool(adjusted and has_adj),
        "has_adjusted": has_adj,
    }
