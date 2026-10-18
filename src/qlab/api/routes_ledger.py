"""Ledger endpoints: research questions with their trial counts, and every trial."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import ledger

router = APIRouter(prefix="/ledger")


@router.get("/questions")
def questions(include_calibration: bool = False) -> list[dict[str, Any]]:
    """Research questions with N, the best Sharpe and the best edge Sharpe.

    Args:
        include_calibration: Also list calibration's simulated questions.

    Returns:
        One row per question, most recent first.
    """
    return ledger.questions(include_calibration)


@router.get("/trials")
def trials(
    question: str | None = None, source: str | None = None, limit: int = 500, offset: int = 0
) -> list[dict[str, Any]]:
    """Trials, newest first.

    Args:
        question: Only this research question.
        source: Only this origin (``studio``, ``gauntlet``, ``lockbox``, ``calibration``...).
        limit: Page size, at most 5,000.
        offset: Trials to skip.

    Returns:
        Trial rows with params and metrics decoded.
    """
    return ledger.list_trials(min(limit, 5000), question, source, offset)


@router.get("/counts")
def counts() -> dict[str, Any]:
    """Trial totals.

    Returns:
        ``total`` and ``by_source``.
    """
    by_source = ledger.counts_by_source()
    return {"total": sum(by_source.values()), "by_source": by_source}
