"""Registry endpoints: every report card with its lifecycle state, and retiring a card.

Promoting a card to paper trading is ``POST /api/paper/entries`` (the paper book owns that action).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import registry

router = APIRouter(prefix="/registry")


class RetireRequest(BaseModel):
    """Body of ``POST /api/registry/{card_id}/retire``.

    Attributes:
        reason: Why the card is retired (required).
    """

    reason: str


@router.get("")
def get_registry() -> dict[str, Any]:
    """Every card with its state, the states in board order and how many cards each holds.

    Returns:
        ``states`` (``key, label, description``), ``counts`` and ``cards`` (see ``registry.cards``).
    """
    items = registry.cards()
    return {
        "states": [{"key": s, "label": registry.LABELS[s], "description": registry.DESCRIPTIONS[s]}
                   for s in registry.STATES],
        "counts": registry.counts(items),
        "cards": items,
    }


@router.post("/{card_id}/retire")
def retire_card(card_id: str, req: RetireRequest) -> dict[str, Any]:
    """Retire a card; an active paper entry for it stops too.

    Args:
        card_id: Report card id.
        req: Reason.

    Returns:
        The card with its new state.

    Raises:
        HTTPException: 404 for an unknown card; 409 without a reason or when already retired.
    """
    try:
        return registry.retire(card_id, req.reason, actor="web")
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except registry.Refused as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
