"""Risk controls outside any strategy: the kill switch, pauses, and limits, with friction only on adding risk.

Design (ROADMAP §6b, "Asymmetric friction"):

- **Reducing risk is instant.** ``kill`` (stop everything and flatten, done by ``execution.kill``), ``pause`` (no new
  orders, positions kept) and tightening a limit take effect the moment they are recorded.
- **Adding risk waits.** ``resume`` (after a kill or a pause) and loosening a limit need a typed confirmation and a
  written reason, and take effect 24 hours later. Anything recorded in between can override them.
- **One append-only log.** Every action is a row of ``broker_controls`` with the time it was recorded and the time it
  takes effect. The state at any moment comes from the rows already in effect: the one recorded last wins. So a kill
  after a pending resume wins, and so does a tightening recorded while a loosening waits.
- Nothing here talks to a broker. ``execution`` asks ``allowed`` before every order.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .. import audit, db
from ..timeutil import naive_utc, now_utc

COOLING_OFF = timedelta(hours=24)
RESUME_WORD = "RESUME"
LOOSEN_WORD = "LOOSEN"

DEFAULT_LIMITS: dict[str, float] = {
    "sleeve_capital": 10_000.0,     # paper money given to each entry, in the broker's currency (USD or USDT)
    "max_order_notional": 12_000.0,  # largest single order, in the broker's currency
    "max_sleeve_gross": 1.0,         # an entry's holdings at most this times its own equity (no leverage)
    "max_account_share": 0.9,        # all entries together at most this share of the paper account's equity
    "max_orders_per_day": 40,        # orders sent per UTC day across every entry
    "min_order_notional": 10.0,      # smaller trades are skipped as dust
}
# Which direction tightens each limit: "lower" means a smaller value is safer.
TIGHTER = {"sleeve_capital": "lower", "max_order_notional": "lower", "max_sleeve_gross": "lower",
           "max_account_share": "lower", "max_orders_per_day": "lower", "min_order_notional": "higher"}


def _rows(now: datetime) -> list[dict[str, Any]]:
    """Control rows in effect at ``now``, oldest recorded first.

    Args:
        now: Time (tz-aware).

    Returns:
        Rows with ``payload`` decoded.
    """
    rows = db.rows("SELECT * FROM broker_controls WHERE effective_at <= ? ORDER BY ts, effective_at", [naive_utc(now)])
    for r in rows:
        r["payload"] = json.loads(r["payload"] or "{}")
    return rows


def _record(action: str, scope: str, reason: str, actor: str, now: datetime, effective: datetime,
            payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append one control row and its audit event.

    Args:
        action: ``kill``, ``pause``, ``resume`` or ``limits``.
        scope: ``all`` or an entry id.
        reason: Why.
        actor: Who.
        now: When it was recorded (tz-aware).
        effective: When it takes effect (tz-aware).
        payload: Extra data (the full limits for ``limits``).

    Returns:
        The row as stored.
    """
    row = {"ts": naive_utc(now), "action": action, "scope": scope, "reason": reason, "actor": actor,
           "effective_at": naive_utc(effective), "payload": db.dumps(payload or {})}
    with db.connect() as con:
        con.execute("INSERT INTO broker_controls VALUES (?, ?, ?, ?, ?, ?, ?)", list(row.values()))
    audit.log(f"broker.{action}", target=scope, actor=actor, reason=reason,
              details={"effective_at": row["effective_at"].isoformat(), **(payload or {})})
    return row


def limits(now: datetime | None = None) -> dict[str, float]:
    """The limits in force.

    Args:
        now: Time (tz-aware); None means now.

    Returns:
        Every limit in ``DEFAULT_LIMITS``, from the latest recorded version already in effect.
    """
    rows = [r for r in _rows(now or now_utc()) if r["action"] == "limits"]
    return {**DEFAULT_LIMITS, **(rows[-1]["payload"] if rows else {})}


def loosens(old: dict[str, float], new: dict[str, float]) -> list[str]:
    """The limits a change would loosen.

    Args:
        old: Limits in force.
        new: Proposed limits.

    Returns:
        Names of the limits that move in the riskier direction.
    """
    out = []
    for k, direction in TIGHTER.items():
        if k in new and (new[k] > old[k] if direction == "lower" else new[k] < old[k]):
            out.append(k)
    return out


def set_limits(changes: dict[str, float], reason: str, actor: str = "user", confirm: str = "",
               now: datetime | None = None) -> dict[str, Any]:
    """Change limits: at once when every change tightens, after 24 hours when any loosens.

    Args:
        changes: New values by limit name.
        reason: Why (required).
        actor: Who.
        confirm: Must be ``LOOSEN`` when any change loosens.
        now: Time (tz-aware); None means now.

    Returns:
        ``limits`` (the new set), ``effective_at`` and ``loosened`` (names).

    Raises:
        ValueError: An unknown limit, a non-positive value, a missing reason, or a loosening without the word.
    """
    now = now or now_utc()
    if not reason.strip():
        raise ValueError("a reason is required to change a limit")
    unknown = set(changes) - set(DEFAULT_LIMITS)
    if unknown:
        raise ValueError(f"unknown limits: {sorted(unknown)}")
    if any(float(v) <= 0 for v in changes.values()):
        raise ValueError("limits must be positive")
    current = limits(now)
    new = {**current, **{k: float(v) for k, v in changes.items()}}
    loose = loosens(current, new)
    if loose and confirm.strip() != LOOSEN_WORD:
        raise ValueError(f"loosening {', '.join(loose)} adds risk: type {LOOSEN_WORD} to confirm; it takes effect "
                         "24 hours from now")
    effective = now + COOLING_OFF if loose else now
    _record("limits", "all", reason, actor, now, effective, new)
    return {"limits": new, "effective_at": effective.isoformat(), "loosened": loose}


def pause(scope: str, reason: str, actor: str = "user", now: datetime | None = None) -> dict[str, Any]:
    """Stop new orders for one entry or for all, keeping positions. Takes effect at once.

    Args:
        scope: ``all`` or an entry id.
        reason: Why (required).
        actor: Who.
        now: Time (tz-aware); None means now.

    Returns:
        The control row.

    Raises:
        ValueError: Missing reason.
    """
    if not reason.strip():
        raise ValueError("a reason is required")
    now = now or now_utc()
    return _record("pause", scope, reason, actor, now, now)


def record_kill(reason: str, actor: str = "user", now: datetime | None = None) -> dict[str, Any]:
    """Record the kill switch: no new orders anywhere. ``execution.kill`` also cancels and flattens.

    Args:
        reason: Why (may be short; the kill must never be slowed down by a form).
        actor: Who.
        now: Time (tz-aware); None means now.

    Returns:
        The control row.
    """
    now = now or now_utc()
    return _record("kill", "all", reason or "kill switch", actor, now, now)


def resume(scope: str, reason: str, confirm: str, actor: str = "user", now: datetime | None = None) -> dict[str, Any]:
    """Ask to lift a kill or a pause. It takes effect 24 hours from now unless something newer overrides it.

    Args:
        scope: ``all`` or an entry id.
        reason: Why (required).
        confirm: Must be ``RESUME``.
        actor: Who.
        now: Time (tz-aware); None means now.

    Returns:
        The control row, with ``effective_at``.

    Raises:
        ValueError: Missing reason or confirmation.
    """
    if not reason.strip():
        raise ValueError("a reason is required to resume")
    if confirm.strip() != RESUME_WORD:
        raise ValueError(f"resuming adds risk: type {RESUME_WORD} to confirm; it takes effect 24 hours from now")
    now = now or now_utc()
    return _record("resume", scope, reason, actor, now, now + COOLING_OFF)


def state(now: datetime | None = None) -> dict[str, Any]:
    """Whether new orders are allowed, globally and per entry, and what is waiting to take effect.

    Args:
        now: Time (tz-aware); None means now.

    Returns:
        ``mode`` (``active``, ``paused`` or ``killed``), ``since`` and ``reason`` of the global state,
        ``entries`` (entry id to ``paused`` or ``active``, only for entries with a pause or resume),
        ``pending`` (rows recorded but not yet in effect) and ``limits``.
    """
    now = now or now_utc()
    rows = _rows(now)
    mode, since, reason = "active", None, None
    entries: dict[str, str] = {}
    for r in rows:
        if r["action"] == "limits":
            continue
        if r["scope"] == "all":
            if r["action"] == "kill":
                mode, since, reason = "killed", r["ts"], r["reason"]
            elif r["action"] == "pause" and mode != "killed":
                mode, since, reason = "paused", r["ts"], r["reason"]
            elif r["action"] == "resume":
                mode, since, reason = "active", r["effective_at"], r["reason"]
        else:
            entries[r["scope"]] = "paused" if r["action"] == "pause" else "active"
    pending = db.rows("SELECT ts, action, scope, reason, actor, effective_at FROM broker_controls "
                      "WHERE effective_at > ? ORDER BY effective_at", [naive_utc(now)])
    return {"mode": mode, "since": since, "reason": reason, "entries": entries, "pending": pending,
            "limits": limits(now)}


def allowed(entry_id: str, now: datetime | None = None) -> tuple[bool, str]:
    """Whether an entry may send new orders now.

    Args:
        entry_id: Paper-book entry.
        now: Time (tz-aware); None means now.

    Returns:
        ``(ok, reason)``; reason says what blocks it.
    """
    st = state(now)
    if st["mode"] != "active":
        return False, f"trading is {st['mode']} ({st['reason']})"
    if st["entries"].get(entry_id) == "paused":
        return False, "this entry is paused"
    return True, ""
