"""Radar endpoints (M10, decision 0016): anomalies, the scenario board, signal charts, and the collection passes.

Reads never fetch or scan. The ``POST`` passes run inside this process, the database's one writer, so the server's
timer sends its 15-minute and daily passes here. Nothing here can add risk: the only control the radar touches is the
opt-in pause of new paper orders.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..radar import collect, overview, scan, scenarios, store

router = APIRouter(prefix="/radar")


class PassRequest(BaseModel):
    """Body of ``POST /api/radar/collect`` and ``POST /api/radar/run``.

    Attributes:
        kind: ``quick`` (every 15 minutes) or ``deep`` (daily, with history and GDELT).
        only: Collect only these sources; empty means the pass's list.
        recalibrate: For ``run``: recalibrate every detector before scanning.
    """

    kind: str = "quick"
    only: list[str] = []
    recalibrate: bool = False


class ScanRequest(BaseModel):
    """Body of ``POST /api/radar/scan``.

    Attributes:
        recalibrate: Recalibrate every detector before scanning.
    """

    recalibrate: bool = False


@router.get("")
def radar_overview() -> dict[str, Any]:
    """Everything the Radar page shows.

    Returns:
        See ``radar.overview.overview``.
    """
    return overview.overview()


@router.get("/signal")
def signal(key: str, days: int = 730) -> dict[str, Any]:
    """One signal's chart with its detectors' statistics and thresholds.

    Args:
        key: Signal key, e.g. ``wiki/bank_run``.
        days: Days of history.

    Returns:
        See ``radar.overview.signal_view``.

    Raises:
        HTTPException: 404 for an unknown signal.
    """
    try:
        return overview.signal_view(key, days)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from None


@router.get("/scenarios")
def scenario_board() -> dict[str, Any]:
    """The scenario board.

    Returns:
        See ``radar.scenarios.board``.
    """
    return scenarios.board()


@router.get("/anomalies")
def anomalies(status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
    """Anomaly episodes.

    Args:
        status: ``open``, ``closed`` or ``all``.
        limit: Maximum rows.

    Returns:
        Rows of ``radar_anomalies``.
    """
    return scan.anomalies(None if status == "all" else status, limit)


@router.get("/headlines")
def headlines(topic: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Stored headlines (untrusted text, shown as data).

    Args:
        topic: Only this GDELT query id or HN keyword.
        limit: Maximum rows.

    Returns:
        Rows of ``radar_headlines``, newest first.
    """
    return store.headlines(limit, topic)


@router.get("/calibration")
def calibration() -> dict[str, Any] | None:
    """The latest calibration with every signal's thresholds and measured rates.

    Returns:
        See ``radar.scan.calibrate``; None before the first.
    """
    return scan.latest_calibration()


@router.post("/collect")
def run_collect(req: PassRequest) -> dict[str, Any]:
    """One collection pass.

    Args:
        req: Pass kind and sources.

    Returns:
        See ``radar.collect.run``.

    Raises:
        HTTPException: 400 for an unknown pass or source.
    """
    try:
        return collect.run(req.kind, req.only or None, actor="api")
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None


@router.post("/scan")
def run_scan(req: ScanRequest) -> dict[str, Any]:
    """One scan.

    Args:
        req: Whether to recalibrate.

    Returns:
        See ``radar.scan.run`` (without the per-detector readings).
    """
    out = scan.run(recalibrate=req.recalibrate, actor="api")
    return {k: v for k, v in out.items() if k != "readings"}


@router.post("/run")
def run_pass(req: PassRequest) -> dict[str, Any]:
    """A collection pass followed by a scan: what the server's timer runs.

    Args:
        req: Pass kind, sources and whether to recalibrate.

    Returns:
        ``collect`` and ``scan`` summaries.

    Raises:
        HTTPException: 400 for an unknown pass or source.
    """
    try:
        col = collect.run(req.kind, req.only or None, actor="api")
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from None
    out = scan.run(recalibrate=req.recalibrate, actor="api")
    return {"collect": col, "scan": {k: v for k, v in out.items() if k != "readings"}}
