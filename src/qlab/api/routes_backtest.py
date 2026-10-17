"""Backtest endpoints: strategy registry, instruments, cost presets, runs and saved reports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import ledger
from ..backtest import runner
from ..backtest.engine import COST_PRESETS
from ..backtest.strategies import REGISTRY
from ..data import store

router = APIRouter(prefix="/backtest")


class RunRequest(BaseModel):
    """Body of ``POST /api/backtest/run``.

    Attributes:
        source: Data source of the instrument.
        symbol: Instrument.
        interval: Bar interval.
        strategy: Registry key.
        params: Parameter overrides.
        costs: ``"auto"``, a preset name, or a dict of cost-model fields in basis points.
        start: First date, ``YYYY-MM-DD``.
        end: Last date, ``YYYY-MM-DD``.
    """

    source: str
    symbol: str
    interval: str = "1d"
    strategy: str = "trend_sma"
    params: dict[str, float] = Field(default_factory=dict)
    costs: str | dict[str, float] = "auto"
    start: str | None = None
    end: str | None = None


@router.get("/strategies")
def strategies() -> list[dict[str, Any]]:
    """Every registered strategy with its parameters.

    Returns:
        ``name, family, description, reference`` and ``params`` (default, low, high, step, doc, is_int).
    """
    return [
        {
            "name": s.name,
            "family": s.family,
            "description": s.description,
            "reference": s.reference,
            "params": {k: {**p.__dict__, "is_int": p.is_int} for k, p in s.params.items()},
        }
        for s in REGISTRY.values()
    ]


@router.get("/instruments")
def instruments() -> list[dict[str, Any]]:
    """Ingested bar series that can be backtested.

    Returns:
        ``source, symbol, interval, first_ts, last_ts, n_rows`` for every bar series.
    """
    keys = ("source", "symbol", "interval", "first_ts", "last_ts", "n_rows")
    return [{k: s[k] for k in keys} for s in store.list_series() if s["kind"] == "bars"]


@router.get("/presets")
def presets() -> dict[str, dict[str, float]]:
    """Cost presets in basis points per side.

    Returns:
        Preset name to cost-model fields.
    """
    return {name: model.__dict__ for name, model in COST_PRESETS.items()}


@router.post("/run")
def run_backtest(req: RunRequest) -> dict[str, Any]:
    """Run a backtest, record it as a ledger trial, and return its report.

    Args:
        req: What to run.

    Returns:
        The report (see ``backtest.runner.report``).

    Raises:
        HTTPException: 404 for an unknown strategy or a series that isn't ingested; 400 for a bad cost preset.
    """
    if req.strategy not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown strategy {req.strategy}")
    try:
        result, summary = runner.backtest(
            req.source, req.symbol, req.interval, req.strategy, req.params, req.costs, req.start, req.end
        )
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except KeyError as err:
        raise HTTPException(status_code=400, detail=f"unknown cost preset {err}") from err
    return runner.report(result, summary)


@router.get("/runs")
def runs(limit: int = 50) -> list[dict[str, Any]]:
    """Recent backtests started from the Studio.

    Args:
        limit: Maximum number of runs.

    Returns:
        Trial rows (newest first) with params and metrics decoded.
    """
    return [t for t in ledger.list_trials(limit * 4) if t["source"] == "studio"][:limit]


@router.get("/report/{trial_id}")
def saved_report(trial_id: str) -> dict[str, Any]:
    """A saved backtest report.

    Args:
        trial_id: Ledger trial id.

    Returns:
        The report.

    Raises:
        HTTPException: 404 when no report exists for the trial.
    """
    try:
        return runner.load_report(trial_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=f"no report for trial {trial_id}") from err
