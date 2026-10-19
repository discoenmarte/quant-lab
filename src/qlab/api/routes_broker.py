"""Broker paper trading endpoints: status, sleeves, the daily steps, and the controls.

Paper only (``broker.base`` refuses any live host). Reducing risk (kill, pause, flatten, tighter limits) works with
one request; adding risk (enroll, resume, looser limits) needs a typed confirmation and a reason, and resuming or
loosening waits 24 hours.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..broker import controls, execution
from ..broker.base import BrokerError

router = APIRouter(prefix="/broker")


class Reason(BaseModel):
    """Body with a reason.

    Attributes:
        reason: Why.
    """

    reason: str = ""


class Scoped(BaseModel):
    """Body of ``POST /api/broker/pause`` and ``/resume``.

    Attributes:
        scope: ``all`` or an entry id.
        reason: Why.
        confirm: ``RESUME`` (resume only).
    """

    scope: str = "all"
    reason: str = ""
    confirm: str = ""


class Enroll(BaseModel):
    """Body of ``POST /api/broker/enroll``.

    Attributes:
        entry_id: Paper-book entry.
        reason: Why it starts paper trading.
        confirm: The entry id typed again.
    """

    entry_id: str
    reason: str = ""
    confirm: str = ""


class Limits(BaseModel):
    """Body of ``POST /api/broker/limits``.

    Attributes:
        changes: New values by limit name.
        reason: Why.
        confirm: ``LOOSEN`` when any change loosens.
    """

    changes: dict[str, float] = Field(default_factory=dict)
    reason: str = ""
    confirm: str = ""


def _guard(fn, *args, **kwargs) -> Any:
    """Call a broker function and turn its expected errors into HTTP errors.

    Args:
        fn: Function.
        *args: Its arguments.
        **kwargs: Its keyword arguments.

    Returns:
        Its result.

    Raises:
        HTTPException: 404 for an unknown entry, 400 for a refused request, 502 when a broker can't be reached.
    """
    try:
        return fn(*args, **kwargs)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except BrokerError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


@router.get("/status")
def status() -> dict[str, Any]:
    """Controls, paper accounts, sleeves, latest orders and fills, and slippage. Sends nothing.

    Returns:
        See ``broker.execution.status``.
    """
    return execution.status()


@router.get("/controls")
def get_controls() -> dict[str, Any]:
    """The trading mode, pauses and pending changes, without calling any broker (the top bar polls it).

    Returns:
        See ``broker.controls.state``, plus ``sleeves`` (how many are active).
    """
    return {**controls.state(), "sleeves": len(execution.sleeves("active"))}


@router.post("/enroll")
def enroll(req: Enroll) -> dict[str, Any]:
    """Give a paper-book entry a sleeve.

    Args:
        req: Entry, reason and the entry id typed again.

    Returns:
        The sleeve.
    """
    return _guard(execution.enroll, req.entry_id, req.reason, req.confirm, actor="web")


@router.post("/execute")
def execute() -> list[dict[str, Any]]:
    """Send each sleeve's latest decision, within the limits. Safe to repeat.

    Returns:
        One result per sleeve.
    """
    return _guard(execution.execute, actor="web")


@router.post("/sync")
def sync() -> dict[str, Any]:
    """Fetch open orders' states, store fills, reconcile.

    Returns:
        Counts, errors and reconciliation per broker.
    """
    return _guard(execution.sync)


@router.post("/kill")
def kill(req: Reason) -> dict[str, Any]:
    """The kill switch: stop now, cancel open orders, sell every sleeve.

    Args:
        req: Optional reason.

    Returns:
        The controls after the kill, what was cancelled and the sell orders.
    """
    return execution.kill(req.reason or "kill switch (web)", actor="web")


@router.post("/pause")
def pause(req: Scoped) -> dict[str, Any]:
    """Stop new orders for all or one entry, keeping positions.

    Args:
        req: Scope and reason.

    Returns:
        The control row.
    """
    return _guard(controls.pause, req.scope, req.reason or "paused from the Trading Desk", actor="web")


@router.post("/flatten/{entry_id}")
def flatten(entry_id: str, req: Reason) -> dict[str, Any]:
    """Pause one sleeve and sell what it holds.

    Args:
        entry_id: Entry with a sleeve.
        req: Reason.

    Returns:
        Cancelled orders and the sells sent.
    """
    return _guard(execution.flatten, entry_id, req.reason or "flattened from the Trading Desk", actor="web")


@router.post("/resume")
def resume(req: Scoped) -> dict[str, Any]:
    """Ask to lift a kill or a pause; it takes effect 24 hours later.

    Args:
        req: Scope, reason and ``RESUME``.

    Returns:
        The control row with ``effective_at``.
    """
    return _guard(controls.resume, req.scope, req.reason, req.confirm, actor="web")


@router.get("/limits")
def get_limits() -> dict[str, Any]:
    """The limits in force and any change waiting to take effect.

    Returns:
        ``limits`` and ``pending``.
    """
    st = controls.state()
    return {"limits": st["limits"], "pending": [p for p in st["pending"] if p["action"] == "limits"],
            "tighter": controls.TIGHTER}


@router.post("/limits")
def set_limits(req: Limits) -> dict[str, Any]:
    """Change limits: tightening now, loosening after 24 hours with ``LOOSEN``.

    Args:
        req: Changes, reason and confirmation.

    Returns:
        The new limits, when they take effect and which were loosened.
    """
    return _guard(controls.set_limits, req.changes, req.reason, actor="web", confirm=req.confirm)
