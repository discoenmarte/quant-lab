"""Intraday Cockpit endpoint (M9): the library, its calibration, its verdicts and the selector's latest readings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..intraday import cockpit

router = APIRouter(prefix="/intraday")


@router.get("")
def overview() -> dict[str, Any]:
    """Everything the Intraday Cockpit shows. Runs no backtest.

    Returns:
        See ``intraday.cockpit.overview``.
    """
    return cockpit.overview()
