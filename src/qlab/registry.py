"""The strategy registry: every gauntlet report card with one lifecycle state.

Design (decision 0008):

- **Derived, not stored.** A card's state is computed each time from three records the lab already keeps: the card
  (verdict and stage results in ``gauntlet_cards``), the paper book (``paper_entries``) and the owner's retirements
  (``registry_events``, append-only). There is no state column to fall out of sync with them.
- **Six states, one per card,** taken in this order of precedence:

  1. ``in_paper_book``: the card has an active entry in the forward paper book.
  2. ``retired``: the owner retired it, or its paper entry was retired and none is active.
  3. ``validated``: it passed every stage, the lockbox included.
  4. ``candidate``: it passed stages 1-9 and its lockbox is still sealed.
  5. ``near_miss``: rejected, and every stage it failed is one of the two that ask whether the result is luck
     (permutation, significance).
  6. ``rejected``: everything else.

- **The verdict stays beside the state.** A rejected near-miss in the paper book is shown as ``in_paper_book`` with
  verdict ``rejected``, so nobody mistakes forward tracking for validation.
- A near-miss is not "almost validated": its edge passed every check of how the rule behaves but was measured too
  imprecisely to tell from luck. Forward days add evidence on exactly that question, which is why the label exists.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import audit, db
from .timeutil import naive_utc

STATES = ["candidate", "validated", "in_paper_book", "near_miss", "rejected", "retired"]
LABELS = {
    "candidate": "Candidate",
    "validated": "Validated",
    "in_paper_book": "In paper book",
    "near_miss": "Near-miss",
    "rejected": "Rejected",
    "retired": "Retired",
}
DESCRIPTIONS = {
    "candidate": "Passed stages 1-9; the lockbox is still sealed.",
    "validated": "Passed every stage, the lockbox included.",
    "in_paper_book": "Tracked in the forward paper book (any verdict).",
    "near_miss": "Rejected only at the luck stages (permutation, significance); worth forward days, not money.",
    "rejected": "Failed at least one stage that tests how the rule behaves.",
    "retired": "Retired by the owner, or its paper entry was retired.",
}
LUCK_STAGES = frozenset({"permutation", "significance"})
HEADLINE_STAGES = {"costs": "edge", "walk_forward": "oos_edge", "permutation": "perm_p", "significance": "dsr"}


class Refused(ValueError):
    """A registry action that isn't allowed: no reason given, or the card is already retired."""


def failed_stages(stages: list[dict[str, Any]]) -> list[str]:
    """Keys of the stages a card failed, in gauntlet order.

    Args:
        stages: Stage rows with ``key`` and ``passed`` (True, False or None).

    Returns:
        Stage keys whose ``passed`` is False. Stages that can't reject (None) are not failures.
    """
    return [s["key"] for s in stages if s.get("passed") is False]


def is_near_miss(verdict: str, stages: list[dict[str, Any]]) -> bool:
    """Whether a card is a near-miss: rejected, and only at the stages that test for luck.

    Such a card passed the look-ahead check, kept a positive edge and CAGR after costs, held up out of sample, on a
    plateau of neighbouring parameters, across regimes and against the validated portfolio (where those stages
    apply). Only the permutation test or the Deflated Sharpe stopped it. A card that failed its lockbox is never a
    near-miss: the lockbox is the out-of-sample verdict.

    Args:
        verdict: Gauntlet verdict, ``candidate``, ``validated`` or ``rejected``.
        stages: Stage rows with ``key`` and ``passed``.

    Returns:
        True when the verdict is ``rejected`` and the failed stages are a non-empty subset of ``LUCK_STAGES``.
    """
    failed = set(failed_stages(stages))
    return verdict == "rejected" and bool(failed) and failed <= LUCK_STAGES


def state_of(verdict: str, stages: list[dict[str, Any]], in_book: bool, retired: bool) -> str:
    """The lifecycle state of one card.

    Args:
        verdict: Gauntlet verdict.
        stages: Stage rows with ``key`` and ``passed``.
        in_book: The card has an active paper-book entry.
        retired: The owner retired the card, or one of its paper entries was retired.

    Returns:
        One of ``STATES``, by the precedence in the module docstring.
    """
    if in_book:
        return "in_paper_book"
    if retired:
        return "retired"
    if verdict in ("validated", "candidate"):
        return verdict
    return "near_miss" if is_near_miss(verdict, stages) else "rejected"


def _latest(*values: Any) -> str | None:
    """The latest of some ISO timestamps.

    Args:
        values: ISO strings or None.

    Returns:
        The latest one, or None when all are None.
    """
    present = [str(v) for v in values if v]
    return max(present) if present else None


def cards() -> list[dict[str, Any]]:
    """Every report card with its lifecycle state, newest first.

    Returns:
        One dict per card: ``card_id, created_at, question, family, strategy, scope, params, verdict,
        thresholds_version``, ``failed`` (stage keys), ``near_miss`` (the rule, whatever the state), ``state``,
        ``label``, ``since`` (when it entered the state, ISO UTC), ``entry_id`` (active paper entry or None),
        ``interval`` and the headline stage values ``edge``, ``oos_edge``, ``perm_p`` and ``dsr``.
    """
    rows = db.rows("SELECT * FROM gauntlet_cards ORDER BY created_at DESC, card_id")
    entries = db.rows("SELECT entry_id, card_id, status, added_at, retired_at FROM paper_entries ORDER BY added_at")
    events = db.rows("SELECT card_id, max(ts) AS ts FROM registry_events WHERE action = 'retire' GROUP BY card_id")
    openings = db.rows("SELECT card_id, max(used_at) AS used_at FROM lockbox_uses GROUP BY card_id")
    active = {e["card_id"]: e for e in entries if e["status"] == "active"}
    retired_entry: dict[str, str] = {}
    for e in entries:
        if e["status"] == "retired":
            retired_entry[e["card_id"]] = _latest(retired_entry.get(e["card_id"]), e["retired_at"])
    retired_event = {e["card_id"]: e["ts"] for e in events}
    opened = {o["card_id"]: o["used_at"] for o in openings}

    out = []
    for r in rows:
        cid = r["card_id"]
        stages = json.loads(r["stages"] or "[]")
        retired_at = _latest(retired_entry.get(cid), retired_event.get(cid))
        state = state_of(r["verdict"], stages, cid in active, retired_at is not None)
        if state == "in_paper_book":
            since = active[cid]["added_at"]
        elif state == "retired":
            since = retired_at
        else:  # validated, or rejected at the lockbox, since the opening; otherwise since the card was made
            since = opened.get(cid) or r["created_at"]
        values = {s["key"]: s.get("value") for s in stages}
        out.append({
            "card_id": cid,
            "created_at": r["created_at"],
            "question": r["question"],
            "family": r["family"],
            "strategy": r["strategy"],
            "scope": r["symbol"],
            "params": json.loads(r["params"] or "{}"),
            "verdict": r["verdict"],
            "thresholds_version": r["thresholds_version"],
            "failed": failed_stages(stages),
            "near_miss": is_near_miss(r["verdict"], stages),
            "state": state,
            "label": LABELS[state],
            "since": since,
            "entry_id": (active.get(cid) or {}).get("entry_id"),
            "interval": r["question"].rsplit(":", 1)[-1],
            **{name: values.get(key) for key, name in HEADLINE_STAGES.items()},
        })
    return out


def counts(items: list[dict[str, Any]]) -> dict[str, int]:
    """Number of cards in each state.

    Args:
        items: Output of ``cards``.

    Returns:
        Every state of ``STATES`` mapped to its count (zero included).
    """
    return {s: sum(c["state"] == s for c in items) for s in STATES}


def card(card_id: str) -> dict[str, Any]:
    """One card with its lifecycle state.

    Args:
        card_id: Report card id.

    Returns:
        The card (see ``cards``).

    Raises:
        KeyError: Unknown card.
    """
    found = next((c for c in cards() if c["card_id"] == card_id), None)
    if found is None:
        raise KeyError(f"no report card {card_id}")
    return found


def retire(card_id: str, reason: str, actor: str = "user", now: datetime | None = None) -> dict[str, Any]:
    """Retire a card: it leaves the board's active columns, and its paper entry (if any) stops.

    The card, its trials and its paper decisions all stay; only its state changes. Re-adding the card to the paper
    book later makes it ``in_paper_book`` again.

    Args:
        card_id: Report card id.
        reason: Why, in plain words (required, recorded in the audit log).
        actor: Who retired it.
        now: Time of the action; None means now.

    Returns:
        The card with its new state.

    Raises:
        KeyError: Unknown card.
        Refused: No reason given, or the card is already retired.
    """
    from .paper import book  # the paper book imports the gauntlet; imported here to keep this module light

    before = card(card_id)
    if not reason.strip():
        raise Refused("a reason is required to retire a card")
    if before["state"] == "retired":
        raise Refused(f"card {card_id} is already retired")
    for e in book.entries("active"):
        if e["card_id"] == card_id:
            book.retire(e["entry_id"], reason, actor=actor, now=now)
    with db.connect() as con:
        con.execute("INSERT INTO registry_events (card_id, ts, action, reason, actor) VALUES (?, ?, 'retire', ?, ?)",
                    [card_id, naive_utc(now), reason, actor])
    audit.log("registry.retire", target=card_id, actor=actor, reason=reason,
              details={"question": before["question"], "from": before["state"], "verdict": before["verdict"]})
    return card(card_id)


def snapshot(items: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Every card's state, for comparing one report with the next.

    Args:
        items: Output of ``cards``; None computes it.

    Returns:
        Card id mapped to state.
    """
    return {c["card_id"]: c["state"] for c in (cards() if items is None else items)}
