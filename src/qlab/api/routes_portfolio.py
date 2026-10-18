"""Portfolio endpoints: universes, strategies, runs and saved reports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import ledger
from ..portfolio import runner
from ..portfolio.strategies import REGISTRY

router = APIRouter(prefix="/portfolio")


class PortfolioRequest(BaseModel):
    """Body of ``POST /api/portfolio/run``.

    Attributes:
        universe: Universe name.
        strategy: Portfolio strategy key.
        params: Parameter overrides.
        start: First date, ``YYYY-MM-DD``.
        end: Last date, ``YYYY-MM-DD``.
        include_lockbox: Let the run reach into the sealed lockbox period, which spends that question's lockbox.
    """

    universe: str = "multi_asset"
    strategy: str = "tsmom"
    params: dict[str, float] = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None
    include_lockbox: bool = False


@router.get("/universes")
def universes() -> list[dict[str, Any]]:
    """Named universes with their instruments and first common date.

    Returns:
        ``name, description, members, first, n_days`` per universe.
    """
    return runner.universes()


@router.get("/strategies")
def strategies() -> list[dict[str, Any]]:
    """Every portfolio strategy with its parameters.

    Returns:
        ``name, family, description, reference`` and ``params`` (default, low, high, step, doc, is_int).
    """
    return [
        {"name": s.name, "family": s.family, "description": s.description, "reference": s.reference,
         "params": {k: {**p.__dict__, "is_int": p.is_int} for k, p in s.params.items()}}
        for s in REGISTRY.values()
    ]


@router.post("/run")
def run(req: PortfolioRequest) -> dict[str, Any]:
    """Backtest a portfolio strategy, record it as a ledger trial, and return its report.

    Args:
        req: What to run.

    Returns:
        The report (see ``portfolio.runner.report``).

    Raises:
        HTTPException: 404 for an unknown universe, strategy or missing data; 400 for an empty period.
    """
    if req.strategy not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"unknown strategy {req.strategy}")
    try:
        panel, res, summary = runner.backtest(req.universe, req.strategy, req.params, req.start, req.end,
                                              include_lockbox=req.include_lockbox)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=f"unknown universe {err}") from err
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return runner.report(panel, res, summary)


@router.get("/runs")
def runs(limit: int = 50) -> list[dict[str, Any]]:
    """Recent portfolio runs from the Portfolio Lab.

    Args:
        limit: Maximum number of runs.

    Returns:
        Trial rows, newest first.
    """
    families = {s.family for s in REGISTRY.values()}
    rows = ledger.list_trials(limit * 10, source="studio")
    return [t for t in rows if t["family"] in families and ":" in t["question"]][:limit]


@router.get("/report/{trial_id}")
def saved_report(trial_id: str) -> dict[str, Any]:
    """A saved portfolio report.

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
        raise HTTPException(status_code=404, detail=str(err)) from err
