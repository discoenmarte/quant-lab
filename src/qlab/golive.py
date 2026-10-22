"""The go-live gate (M8): the checklist that must be all green before any real money, computed from evidence.

Design (decision 0013):

- **Evidence, not opinion.** Every item of the roadmap's go-live checklist (ROADMAP §7) is computed from what the
  lab already records: the entry's report card (verdict, stages, thresholds version), the lockbox log, the forward
  paper book (days, band, decay alarm), the broker paper sleeve (fills, measured slippage, a tested kill switch),
  and the owner's written plan (loss budget, stop rule, money they can afford to lose). Nothing here can be ticked by
  hand except the plan, and the plan only counts once the evidence is green.
- **Read-only.** The gate reports; it places no order and changes no limit. There is deliberately no live order path
  in the lab yet: it gets built, with the owner's explicit approval, only once an entry's evidence is all green
  (decision 0013 explains why building it earlier would add risk without adding value).
- **Honest about time.** The paper-trading items need about three months of forward sessions, so the gate shows the
  earliest date each entry could pass them, and why everything else is red.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from . import audit, config, db
from .timeutil import naive_utc, now_utc

MIN_PAPER_DAYS = 63         # about three months of forward sessions (ROADMAP §7: 3-6 months)
MIN_FILLS = 20              # broker paper fills before slippage means anything
SLIPPAGE_TOLERANCE_BPS = 5  # measured slippage may exceed the cost model's by this much
DEFAULT_STOP_RULE = ("Stop and sell everything if the forward edge falls below its band, the decay alarm fires, or "
                     "the live sleeve loses its whole loss budget.")


def _item(key: str, group: str, label: str, ok: bool | None, detail: str) -> dict[str, Any]:
    """One checklist line.

    Args:
        key: Stable id.
        group: ``research``, ``paper``, ``broker`` or ``plan``.
        label: The requirement in plain words.
        ok: True met, False not met, None not measurable yet.
        detail: The numbers behind it.

    Returns:
        The item.
    """
    return {"key": key, "group": group, "label": label, "ok": ok, "detail": detail}


def _stage(card: dict[str, Any], key: str) -> dict[str, Any]:
    """A card's stage by key.

    Args:
        card: Report card.
        key: Stage key.

    Returns:
        The stage, or an empty dict.
    """
    return next((s for s in card.get("stages", []) if s.get("key") == key), {})


def plan(entry_id: str) -> dict[str, Any] | None:
    """The owner's latest written plan for an entry.

    Args:
        entry_id: Paper-book entry.

    Returns:
        The plan row, or None.
    """
    rows = db.rows("SELECT * FROM golive_plans WHERE entry_id = ? ORDER BY created_at DESC LIMIT 1", [entry_id])
    return rows[0] if rows else None


def write_plan(entry_id: str, capital: float, loss_budget: float, stop_rule: str, affirmed: bool, reason: str,
               actor: str = "user") -> dict[str, Any]:
    """Write down the owner's plan for trading an entry with real money. Changes nothing else.

    Args:
        entry_id: Paper-book entry.
        capital: Money the owner would put in, in USD.
        loss_budget: The most the owner accepts to lose before stopping, in USD (at most ``capital``).
        stop_rule: When to stop, in plain words (empty uses ``DEFAULT_STOP_RULE``).
        affirmed: The owner confirms this is money they can afford to lose entirely.
        reason: Why.
        actor: Who.

    Returns:
        The stored plan.

    Raises:
        KeyError: Unknown entry.
        ValueError: A non-positive amount, a loss budget above the capital, or no reason.
    """
    from .paper import book

    book.entry(entry_id)
    if capital <= 0 or loss_budget <= 0:
        raise ValueError("the capital and the loss budget must be positive")
    if loss_budget > capital:
        raise ValueError("the loss budget can't exceed the capital")
    if not reason.strip():
        raise ValueError("a reason is required")
    with db.connect() as con:
        con.execute("INSERT INTO golive_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [entry_id, naive_utc(), float(capital), float(loss_budget), stop_rule.strip() or DEFAULT_STOP_RULE,
                     bool(affirmed), reason.strip(), actor])
    audit.log("golive.plan", target=entry_id, actor=actor, reason=reason,
              details={"capital": capital, "loss_budget": loss_budget, "affirmed": bool(affirmed)})
    return plan(entry_id) or {}


def _sessions_after(day: pd.Timestamp, n: int) -> pd.Timestamp:
    """The weekday ``n`` sessions after a date (holidays not modelled).

    Args:
        day: A date.
        n: Sessions to add.

    Returns:
        That date.
    """
    return (pd.Timestamp(day) + pd.offsets.BDay(max(0, n))).normalize()


def checklist(entry_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Every go-live requirement for one paper-book entry, with whether it is met and why.

    Args:
        entry_id: Paper-book entry.
        now: Time (tz-aware); None means now.

    Returns:
        ``entry_id, question, card_id, verdict``, ``items`` (see ``_item``), ``met`` and ``total``, ``ready`` (every
        item met), ``blocking`` (labels of unmet items), ``earliest_paper_date`` (when the paper-time item could pass
        at the earliest) and ``next_step`` in plain words.

    Raises:
        KeyError: Unknown entry.
    """
    from .broker import controls, execution
    from .gauntlet import pipeline
    from .paper import book, score

    now = now or now_utc()
    e = book.entry(entry_id)
    try:
        card = pipeline.load_card(e["card_id"])
    except FileNotFoundError:
        card = {"verdict": e.get("verdict"), "stages": [], "meta": {}}
    items = []

    verdict = card.get("verdict")
    items.append(_item("validated", "research", "Passed every gauntlet stage, the lockbox included",
                       verdict == "validated",
                       f"verdict {verdict}" + (f", failed at {card['failed_stage']}" if card.get("failed_stage")
                                               else "")))
    sig = _stage(card, "significance")
    n = (card.get("meta") or {}).get("n_trials")
    items.append(_item("full_n", "research", "Deflated Sharpe passed with the ledger's full trial count",
                       sig.get("passed") is True, f"N = {n}; " + (sig.get("summary") or "not judged")[:160]))
    version, created = (card.get("thresholds") or {}).get("version"), card.get("created_at")
    th_rows = db.rows("SELECT created_at FROM thresholds WHERE version = ?", [version]) if version else []
    before = bool(th_rows and created and pd.Timestamp(th_rows[0]["created_at"]) <= pd.Timestamp(created))
    items.append(_item("thresholds_before", "research", "Thresholds were fixed before the card was judged",
                       before if version else None,
                       f"thresholds v{version}, card judged {str(created)[:16]}" if version else "no version on card"))
    uses = db.rows("SELECT card_id FROM lockbox_uses WHERE question = ?", [e["question"]])
    lock = _stage(card, "lockbox")
    items.append(_item("lockbox_once", "research", "Lockbox holdout passed on its first and only use",
                       lock.get("passed") is True and len(uses) == 1 and uses[0]["card_id"] == e["card_id"],
                       f"{len(uses)} lockbox uses on {e['question']}; stage "
                       + ("passed" if lock.get("passed") else "not passed" if lock.get("passed") is False
                          else "not run")))
    div = _stage(card, "diversification")
    items.append(_item("diversifies", "research", "Adds diversification to what is already validated",
                       div.get("passed") is True if div.get("passed") is not None else None,
                       (div.get("summary") or "not judged (no validated portfolio to compare with yet)")[:160]))

    rep = score.load_report(entry_id) or {}
    days = int(rep.get("n_days") or 0)
    decs = book.decisions(entry_id)
    first = pd.Timestamp(str(decs[0]["as_of"])[:10]) if decs else pd.Timestamp(naive_utc(now).date())
    earliest = _sessions_after(first, MIN_PAPER_DAYS)
    items.append(_item("paper_time", "paper", f"At least {MIN_PAPER_DAYS} forward sessions (about three months)",
                       days >= MIN_PAPER_DAYS, f"{days} of {MIN_PAPER_DAYS} sessions; earliest {earliest.date()}"))
    band = (rep.get("band") or {}).get("status")
    alarm = bool((rep.get("alarm") or {}).get("alarm"))
    items.append(_item("paper_band", "paper", "Forward results inside their band, no decay alarm",
                       None if days == 0 else (band in ("inside", "above") and not alarm),
                       f"band {band or 'not scored'}, alarm {'FIRED' if alarm else 'quiet'}"))

    sleeve = next((s for s in execution.sleeves() if s["entry_id"] == entry_id), None)
    slip = execution.slippage(entry_id) if sleeve else []
    filled = int(db.scalar("SELECT count(*) FROM broker_orders WHERE entry_id = ? AND filled_qty > 0",
                           [entry_id]) or 0)
    items.append(_item("broker_fills", "broker", f"At least {MIN_FILLS} broker paper fills",
                       filled >= MIN_FILLS if sleeve else False,
                       f"{filled} filled orders" if sleeve else "no broker sleeve (enroll it on the Trading Desk)"))
    if slip:
        mean = sum(r["slippage_bps"] for r in slip) / len(slip)
        model = slip[0]["model_bps"]
        ok_slip = mean <= model + SLIPPAGE_TOLERANCE_BPS if len(slip) >= MIN_FILLS else None
        detail = f"mean {mean:+.1f} bps over {len(slip)} fills against the model's {model:.1f} bps"
    else:
        ok_slip, detail = None, "no fills measured yet"
    items.append(_item("slippage", "broker", f"Measured slippage within the cost model (+{SLIPPAGE_TOLERANCE_BPS} bps)",
                       ok_slip, detail))
    kills = db.rows("SELECT ts FROM broker_controls WHERE action = 'kill' AND ts >= ? ORDER BY ts DESC LIMIT 1",
                    [sleeve["enrolled_at"]]) if sleeve else []
    items.append(_item("kill_tested", "broker", "Kill switch tested since the sleeve started", bool(kills),
                       f"last kill {str(kills[0]['ts'])[:16]}" if kills else "never tested on this sleeve"))
    limits = controls.limits(now)
    items.append(_item("limits", "broker", "Order, notional and daily limits enforced outside any model",
                       True, f"largest order {limits['max_order_notional']:,.0f}, {limits['max_orders_per_day']:.0f} "
                             f"orders a day, no leverage (max gross {limits['max_sleeve_gross']:g})"))

    p = plan(entry_id)
    items.append(_item("loss_budget", "plan", "Maximum loss written down",
                       bool(p and p["loss_budget"] > 0),
                       f"lose at most {p['loss_budget']:,.0f} of {p['capital']:,.0f} USD" if p else "no plan yet"))
    items.append(_item("stop_rule", "plan", "Stop rule written", bool(p and p["stop_rule"]),
                       (p["stop_rule"] if p else f"suggested: {DEFAULT_STOP_RULE}")[:200]))
    items.append(_item("affordable", "plan", "Money you can afford to lose entirely", bool(p and p["affirmed"]),
                       "affirmed" if p and p["affirmed"] else "not affirmed"))

    met = sum(i["ok"] is True for i in items)
    ready = met == len(items)
    blocking = [i["label"] for i in items if i["ok"] is not True]
    if ready:
        step = ("Every item is green. The lab has no live order path yet (decision 0013): building it is the next "
                "step, and it needs your explicit approval.")
    elif verdict != "validated":
        step = ("This strategy hasn't passed the gauntlet, so it can't go live whatever paper trading shows. Keep it "
                "in the paper book only to measure execution.")
    else:
        step = f"Keep paper trading: {len(blocking)} items left, the paper time no earlier than {earliest.date()}."
    return {"entry_id": entry_id, "question": e["question"], "card_id": e["card_id"], "verdict": verdict,
            "status": e["status"], "items": items, "met": met, "total": len(items), "ready": ready,
            "blocking": blocking, "earliest_paper_date": str(earliest.date()), "next_step": step}


def overview(now: datetime | None = None) -> dict[str, Any]:
    """The gate for every active paper-book entry, best first.

    Args:
        now: Time (tz-aware); None means now.

    Returns:
        ``entries`` (``checklist`` per active entry, most items met first), ``ready`` (entries with every item met),
        ``live_path`` (False: the lab has no live order path) and ``owner`` (the name the plan is signed with).
    """
    from .paper import book

    rows = [checklist(e["entry_id"], now) for e in book.entries() if e["status"] == "active"]
    rows.sort(key=lambda r: (-r["met"], r["question"]))
    return {"entries": rows, "ready": [r["entry_id"] for r in rows if r["ready"]], "live_path": False,
            "owner": config.setting("QLAB_OWNER_NAME", "discoenmarte")}


def days_left(entry: dict[str, Any], now: datetime | None = None) -> int:
    """Calendar days until an entry's paper-time item could pass.

    Args:
        entry: Output of ``checklist``.
        now: Time (tz-aware); None means now.

    Returns:
        Days, at least zero.
    """
    now = now or now_utc()
    return max(0, (pd.Timestamp(entry["earliest_paper_date"]) - pd.Timestamp(naive_utc(now).date())).days)


__all__ = ["checklist", "days_left", "overview", "plan", "write_plan"]
