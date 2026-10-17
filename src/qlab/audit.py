"""Audit log: every action taken through the lab, with who, what, when and why."""

from __future__ import annotations

from typing import Any

from . import db
from .timeutil import naive_utc


def log(action: str, target: str = "", reason: str = "", details: Any = None, actor: str = "user") -> None:
    """Append one event to the audit log.

    Args:
        action: Dotted name of what happened, e.g. ``"data.ingest"`` or ``"gauntlet.run"``.
        target: What it happened to, e.g. a source name or a trial id.
        reason: Why, in the person's own words. Required by the UI for risk-increasing actions.
        details: Extra JSON-serialisable context.
        actor: ``"user"``, ``"scheduler"`` or ``"system"``.
    """
    with db.connect() as con:
        con.execute(
            "INSERT INTO audit (ts, actor, action, target, reason, details) VALUES (?, ?, ?, ?, ?, ?)",
            [naive_utc(), actor, action, target, reason, db.dumps(details or {})],
        )


def recent(limit: int = 200) -> list[dict[str, Any]]:
    """Latest audit events, newest first.

    Args:
        limit: Maximum number of events to return.

    Returns:
        Event dicts with ``id, ts, actor, action, target, reason, details``.
    """
    return db.rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", [limit])
