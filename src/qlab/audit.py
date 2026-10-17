"""Audit log: every action taken through the lab (who, what, when, why)."""

from __future__ import annotations

from typing import Any

from . import db
from .timeutil import naive_utc


def log(action: str, target: str = "", reason: str = "", details: Any = None, actor: str = "user") -> None:
    with db.connect() as con:
        con.execute(
            "INSERT INTO audit (ts, actor, action, target, reason, details) VALUES (?, ?, ?, ?, ?, ?)",
            [naive_utc(), actor, action, target, reason, db.dumps(details or {})],
        )


def recent(limit: int = 200) -> list[dict[str, Any]]:
    return db.rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", [limit])
