"""Go-live gate endpoints (M8): the evidence checklist per paper-book entry, and the owner's written plan.

Read-only apart from the plan, which changes nothing by itself. The lab has no live order path (decision 0013).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import golive

router = APIRouter(prefix="/golive")


class PlanRequest(BaseModel):
    """Body of ``POST /api/golive/{entry_id}/plan``.

    Attributes:
        capital: Money the owner would put in, USD.
        loss_budget: The most the owner accepts to lose, USD.
        stop_rule: When to stop; empty uses the suggested rule.
        affirmed: The owner can afford to lose this money entirely.
        reason: Why.
        confirm: The owner's name, typed.
    """

    capital: float
    loss_budget: float
    stop_rule: str = ""
    affirmed: bool = False
    reason: str = ""
    confirm: str = ""


@router.get("")
def overview() -> dict[str, Any]:
    """The gate for every active paper-book entry.

    Returns:
        See ``golive.overview``.
    """
    return golive.overview()


@router.get("/{entry_id}")
def checklist(entry_id: str) -> dict[str, Any]:
    """One entry's checklist.

    Args:
        entry_id: Paper-book entry.

    Returns:
        See ``golive.checklist``.

    Raises:
        HTTPException: 404 for an unknown entry.
    """
    try:
        return golive.checklist(entry_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err).strip("'")) from err


@router.post("/{entry_id}/plan")
def write_plan(entry_id: str, req: PlanRequest) -> dict[str, Any]:
    """Write down the owner's plan: loss budget, stop rule, affordability. Signed with the owner's typed name.

    Args:
        entry_id: Paper-book entry.
        req: The plan.

    Returns:
        The entry's checklist after the plan.

    Raises:
        HTTPException: 404 for an unknown entry, 400 for a bad plan or a wrong name.
    """
    owner = golive.overview()["owner"]
    if req.confirm.strip() != owner:
        raise HTTPException(status_code=400, detail=f"type your name ({owner}) to sign the plan")
    try:
        golive.write_plan(entry_id, req.capital, req.loss_budget, req.stop_rule, req.affirmed, req.reason,
                          actor="web")
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err).strip("'")) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return golive.checklist(entry_id)
