"""Gauntlet endpoints: run a candidate, read report cards, the funnel, thresholds, the lockbox and calibration."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import lockbox
from ..backtest.strategies import REGISTRY
from ..gauntlet import calibration, pipeline, thresholds
from ..portfolio.strategies import REGISTRY as PORTFOLIOS
from ..timeutil import now_utc

router = APIRouter(prefix="/gauntlet")

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class GauntletRequest(BaseModel):
    """Body of ``POST /api/gauntlet/run``.

    Attributes:
        kind: ``"bars"`` for a single-instrument strategy, ``"portfolio"`` for a portfolio strategy.
        source: Data source of the instrument (bars).
        symbol: Instrument (bars).
        interval: Bar interval (bars).
        universe: Universe name (portfolio).
        strategy: Registry key (single-instrument or portfolio registry, by ``kind``).
        params: Candidate parameters; missing ones take their defaults.
        costs: ``"auto"``, a preset name, or a dict of cost-model fields in basis points (bars).
        seed: Seed for the grid fill, permutations and bootstrap.
    """

    kind: Literal["bars", "portfolio"] = "bars"
    source: str = ""
    symbol: str = ""
    interval: str = "1d"
    universe: str = "multi_asset"
    strategy: str = "trend_sma"
    params: dict[str, float] = Field(default_factory=dict)
    costs: str | dict[str, float] = "auto"
    seed: int = 7


class ThresholdChange(BaseModel):
    """Body of ``POST /api/gauntlet/thresholds``.

    Attributes:
        changes: Threshold names and new values.
        reason: Why, in plain words. Required.
    """

    changes: dict[str, float | str]
    reason: str


class LockboxRequest(BaseModel):
    """Body of ``POST /api/gauntlet/cards/{card_id}/lockbox``.

    Attributes:
        confirm: The card's research question, typed out.
        reason: Why the lockbox is being opened now.
    """

    confirm: str
    reason: str


class CalibrationRequest(BaseModel):
    """Body of ``POST /api/gauntlet/calibrations``.

    Attributes:
        noise_reps: Runs per family and researcher type on noise markets.
        power_reps: Runs per family, edge size and history length on planted-trend markets.
    """

    noise_reps: int = Field(100, ge=2, le=2000)
    power_reps: int = Field(40, ge=2, le=1000)


class RejudgeRequest(BaseModel):
    """Body of ``POST /api/gauntlet/calibrations/{run_id}/rejudge``.

    Attributes:
        changes: Threshold overrides to evaluate.
    """

    changes: dict[str, float | str]


@router.get("/stages")
def stages() -> list[dict[str, str]]:
    """The ten stages in order.

    Returns:
        ``key`` and ``name`` per stage.
    """
    return [{"key": k, "name": n} for k, n in pipeline.STAGES]


@router.post("/run")
def run(req: GauntletRequest) -> dict[str, Any]:
    """Run a candidate through the gauntlet. Takes a few seconds; every grid setting becomes a ledger trial.

    Args:
        req: What to judge.

    Returns:
        The report card.

    Raises:
        HTTPException: 404 for an unknown strategy or missing data; 400 for too little research data.
    """
    registry = PORTFOLIOS if req.kind == "portfolio" else REGISTRY
    if req.strategy not in registry:
        raise HTTPException(status_code=404, detail=f"unknown strategy {req.strategy}")
    try:
        if req.kind == "portfolio":
            return pipeline.run_portfolio_gauntlet(req.universe, req.strategy, req.params, req.seed)
        return pipeline.run_gauntlet(req.source, req.symbol, req.interval, req.strategy, req.params, req.costs,
                                     req.seed)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown universe {err}") from err
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/cards")
def cards(limit: int = 200) -> list[dict[str, Any]]:
    """Recent report cards.

    Args:
        limit: Maximum number of cards.

    Returns:
        Catalog rows, newest first, with per-stage pass/fail.
    """
    return pipeline.list_cards(limit)


@router.get("/cards/{card_id}")
def card(card_id: str) -> dict[str, Any]:
    """One full report card.

    Args:
        card_id: Card id.

    Returns:
        The card with every stage's chart data.

    Raises:
        HTTPException: 404 for an unknown card.
    """
    try:
        return pipeline.load_card(card_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post("/cards/{card_id}/lockbox")
def open_lockbox(card_id: str, req: LockboxRequest) -> dict[str, Any]:
    """Open the lockbox for a candidate: once per research question, with typed confirmation and a reason.

    Args:
        card_id: Card id.
        req: Confirmation and reason.

    Returns:
        The updated card.

    Raises:
        HTTPException: 404 for an unknown card; 400 when the confirmation, reason or card state doesn't allow it.
    """
    try:
        return pipeline.open_lockbox(card_id, req.confirm, req.reason)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/funnel")
def funnel() -> list[dict[str, Any]]:
    """How many cards reached and passed each stage.

    Returns:
        One row per stage.
    """
    return pipeline.funnel()


@router.get("/thresholds")
def get_thresholds() -> dict[str, Any]:
    """The thresholds in force and every earlier version.

    Returns:
        ``version``, ``config`` and ``history`` (newest first).
    """
    version, cfg = thresholds.current()
    return {"version": version, "config": cfg, "history": thresholds.history()}


@router.post("/thresholds")
def set_thresholds(req: ThresholdChange) -> dict[str, Any]:
    """Create a new threshold version. Only future cards use it.

    Args:
        req: Changes and the reason.

    Returns:
        The new ``version`` and ``config``.

    Raises:
        HTTPException: 400 for an empty reason or an invalid key or value.
    """
    try:
        version = thresholds.update(req.changes, req.reason)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return {"version": version, "config": thresholds.current()[1]}


@router.get("/lockbox")
def lockbox_state() -> dict[str, Any]:
    """Where the lockbox starts and which research questions have already used theirs.

    Returns:
        ``sealed_from``, ``research_end`` and ``spent`` (one row per question).
    """
    return {"sealed_from": str(lockbox.sealed_from().date()), "research_end": lockbox.research_end(),
            "spent": lockbox.spent_questions()}


def _calibrate(job_id: str, noise_reps: int, power_reps: int) -> None:
    """Worker thread body: run a calibration study and record progress on the job.

    Args:
        job_id: Key into ``_jobs``.
        noise_reps: Noise runs per family and researcher type.
        power_reps: Planted-edge runs per setting.
    """
    job = _jobs[job_id]

    def log(msg: str) -> None:
        job["log"].append(f"{now_utc().strftime('%H:%M:%S')} {msg}")

    try:
        rep = calibration.run_calibration(noise_reps=noise_reps, power_reps=power_reps, log=log)
        job["report_id"] = rep["id"]
        job["status"] = "done"
    except Exception as err:  # surfaced in the UI instead of dying silently in the thread
        job["status"] = "failed"
        log(f"calibration failed: {err}")
    finally:
        job["finished_at"] = now_utc().isoformat(timespec="seconds")


@router.post("/calibrations")
def start_calibration(req: CalibrationRequest) -> dict[str, Any]:
    """Start a calibration study in the background. It uses every core but one for several minutes.

    Args:
        req: Study size.

    Returns:
        The new job: ``id, status, started_at, log``.

    Raises:
        HTTPException: 409 while another calibration is running.
    """
    with _lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            raise HTTPException(status_code=409, detail="a calibration is already running")
        job_id = uuid.uuid4().hex[:8]
        _jobs[job_id] = {"id": job_id, "status": "running", "started_at": now_utc().isoformat(timespec="seconds"),
                         "finished_at": None, "log": [], "report_id": None, "settings": req.model_dump()}
    threading.Thread(target=_calibrate, args=(job_id, req.noise_reps, req.power_reps), daemon=True).start()
    return _jobs[job_id]


@router.get("/calibrations/jobs")
def calibration_jobs() -> list[dict[str, Any]]:
    """Calibration jobs started since the server came up.

    Returns:
        Jobs, newest first.
    """
    return sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)


@router.get("/calibrations")
def calibrations() -> list[dict[str, Any]]:
    """Saved calibration reports, without per-run detail.

    Returns:
        Reports, newest first.
    """
    return calibration.list_reports()


@router.get("/calibrations/{run_id}")
def calibration_report(run_id: str) -> dict[str, Any]:
    """One calibration report, without per-run detail.

    Args:
        run_id: Report id.

    Returns:
        The report.

    Raises:
        HTTPException: 404 for an unknown report.
    """
    try:
        rep = calibration.load_report(run_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    rep.pop("runs", None)
    return rep


@router.post("/calibrations/{run_id}/rejudge")
def rejudge(run_id: str, req: RejudgeRequest) -> dict[str, Any]:
    """What a saved calibration says under other thresholds, without re-running anything.

    Args:
        run_id: Report id.
        req: Threshold overrides.

    Returns:
        ``noise``, ``power``, ``control`` and ``fpr_overall``.

    Raises:
        HTTPException: 404 for an unknown report, 400 when its runs lack a value the changes need.
    """
    try:
        return calibration.rejudge(run_id, req.changes)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
