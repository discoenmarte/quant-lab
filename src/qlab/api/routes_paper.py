"""Paper book endpoints: the book, one entry's forward report, adding and retiring cards, and the daily run and score.

Paper only. Nothing here can place an order: the book records targets and scores them on stored bars.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..gauntlet.pipeline import list_cards
from ..paper import book, score

router = APIRouter(prefix="/paper")


class AddRequest(BaseModel):
    """Body of ``POST /api/paper/entries``.

    Attributes:
        card_id: Gauntlet report card to track (any verdict).
        note: Why it is tracked.
    """

    card_id: str
    note: str = ""


class RetireRequest(BaseModel):
    """Body of ``POST /api/paper/entries/{entry_id}/retire``.

    Attributes:
        reason: Why the entry stops (required).
    """

    reason: str


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    """One row of the book table: the entry, its latest decision and its latest forward report's headline numbers.

    Args:
        item: Entry row (decoded).

    Returns:
        The entry's columns plus ``n_decisions``, ``last_decision``, ``late``, ``through``, ``n_days``,
        ``days_in_book``, ``forward_return``, ``benchmark_return``, ``band`` and ``alarm``.
    """
    decs = book.decisions(item["entry_id"])
    rep = score.load_report(item["entry_id"]) or {}
    stats = rep.get("stats") or {}
    out = {k: item[k] for k in ("entry_id", "card_id", "kind", "question", "family", "strategy", "scope", "params",
                                "verdict", "added_at", "status", "note", "retired_at")}
    out.update(
        research_edge_sharpe=item["reference"].get("edge_sharpe"),
        n_decisions=len(decs),
        last_decision=decs[-1] if decs else None,
        late=sum(d["lead_hours"] < 0 for d in decs),
        scored_at=rep.get("scored_at"),
        through=rep.get("through"),
        n_days=rep.get("n_days", 0),
        days_in_book=rep.get("days_in_book"),
        forward_return=(stats.get("strategy") or {}).get("total_return"),
        benchmark_return=(stats.get("benchmark") or {}).get("total_return"),
        band=rep.get("band"),
        alarm=rep.get("alarm"),
    )
    return out


@router.get("/book")
def get_book() -> dict[str, Any]:
    """Every entry with its headline forward numbers, and the cards that could join.

    Returns:
        ``entries`` (see ``_summary``) and ``eligible`` (daily or portfolio cards not active in the book, newest
        first).
    """
    items = book.entries()
    active = {e["card_id"] for e in items if e["status"] == "active"}
    eligible = [c for c in list_cards() if c["question"].endswith(":1d") and c["card_id"] not in active]
    return {"entries": [_summary(e) for e in items], "eligible": eligible}


@router.get("/entries/{entry_id}")
def get_entry(entry_id: str) -> dict[str, Any]:
    """One entry: its row, every decision and the latest forward report.

    Args:
        entry_id: Entry id.

    Returns:
        ``entry`` (see ``_summary``), ``decisions`` (newest first) and ``report`` (None before the first scoring).

    Raises:
        HTTPException: 404 for an unknown entry.
    """
    try:
        item = book.entry(entry_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return {"entry": _summary(item), "decisions": book.decisions(entry_id)[::-1],
            "report": score.load_report(entry_id)}


@router.post("/entries")
def add_entry(req: AddRequest) -> dict[str, Any]:
    """Put a card in the book (re-simulates its research period, recorded in the ledger).

    Args:
        req: Card and note.

    Returns:
        The new entry (see ``_summary``).

    Raises:
        HTTPException: 404 for an unknown card or missing data; 409 when the book refuses it.
    """
    try:
        return _summary(book.add(req.card_id, req.note, actor="web"))
    except book.Refused as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post("/entries/{entry_id}/retire")
def retire_entry(entry_id: str, req: RetireRequest) -> dict[str, Any]:
    """Stop an entry: no new decisions, scoring ends now, its record stays.

    Args:
        entry_id: Entry id.
        req: Reason.

    Returns:
        The updated entry (see ``_summary``).

    Raises:
        HTTPException: 404 for an unknown entry; 409 without a reason or when already retired.
    """
    try:
        return _summary(book.retire(entry_id, req.reason, actor="web"))
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except book.Refused as err:
        raise HTTPException(status_code=409, detail=str(err)) from err


@router.post("/run")
def run_book() -> list[dict[str, Any]]:
    """Decide every active entry for the latest final close. Backfills and stale data are refused.

    Returns:
        One result per entry (see ``paper.book.decide``).
    """
    return book.run(actor="web")


@router.post("/score")
def score_book() -> list[dict[str, Any]]:
    """Score every entry on the bars after its decisions.

    Returns:
        One short result per entry (see ``paper.score.score``).
    """
    return score.score(actor="web")
