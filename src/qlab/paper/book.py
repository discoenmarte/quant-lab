"""The forward paper book: which report cards are tracked, and the target each one decides after every close.

Design:

- **Append-only decisions.** A decision is the target an entry's strategy computes from data up to one close
  (``as_of``), stored with the UTC time it was made (``decided_at``) and the first open it can be filled at
  (``next_open``). Rows are inserted, never updated or deleted, one per entry and date.
- **Before the outcome exists.** ``run`` decides only on the latest bar whose close is final, and refuses a backfill
  (a later final bar is already stored, so the decision would be taken after its outcome was known) and stale data
  (the next session has already closed in the world but isn't stored yet: ingest first). A decision stored after
  the next open is accepted but marked late, because filling it at that open is optimistic.
- **Same code as research.** Targets come from the card's own subject (``gauntlet.subjects``) and strategy, on the
  data clipped at the decision's close, so the book runs exactly what the gauntlet judged.
- **Frozen expectations.** Adding a card re-simulates its research period (recorded in the ledger with source
  ``paper``) and stores the daily statistics that the forward bands and the decay alarm compare against.
- **Any verdict.** Rejected near-misses are worth tracking: forward days are free evidence. The verdict is stored
  with the entry so nobody mistakes one for a validated strategy.
- Nothing here places orders. The book records intentions; broker paper accounts come with M7b.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .. import audit, code_version, db, ledger
from ..backtest import metrics
from ..data import store
from ..data.ingest import SOURCES
from ..gauntlet.pipeline import load_card
from ..gauntlet.subjects import BarSubject, PanelSubject, subject_from_meta
from ..portfolio.runner import panel_hash
from ..timeutil import naive_utc, now_utc
from . import monitor, sessions

PAPER_SOURCE = "paper"
SUBJECT_KEYS = ("kind", "source", "symbol", "universe", "interval", "strategy", "costs", "lockbox_from")


class Refused(ValueError):
    """An action the book won't take: a backfill, stale data, a duplicate entry or an unsupported card."""


def paper_question(question: str) -> str:
    """Ledger question for an entry's paper trials, kept apart from the research question.

    A separate key means forward scoring never spends the research question's lockbox and never counts as a new
    trial when a later candidate is deflated.

    Args:
        question: The card's research question, e.g. ``"trend_filter:all_etfs:1d"``.

    Returns:
        ``"paper:" + question``.
    """
    return f"paper:{question}"


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    """Turn an entry row's JSON columns into values.

    Args:
        row: Row of ``paper_entries``.

    Returns:
        The row with ``params`` and ``reference`` as dicts.
    """
    row["params"] = json.loads(row.get("params") or "{}")
    row["reference"] = json.loads(row.get("reference") or "{}")
    return row


def entries(status: str | None = None) -> list[dict[str, Any]]:
    """Entries of the book, oldest first.

    Args:
        status: Only ``"active"`` or ``"retired"`` entries; None returns all.

    Returns:
        Entry rows with ``params`` and ``reference`` decoded.
    """
    where, args = ("WHERE status = ?", [status]) if status else ("", [])
    return [_decode(r) for r in db.rows(f"SELECT * FROM paper_entries {where} ORDER BY added_at, entry_id", args)]


def entry(entry_id: str) -> dict[str, Any]:
    """One entry.

    Args:
        entry_id: Entry id.

    Returns:
        The entry row, decoded.

    Raises:
        KeyError: Unknown entry.
    """
    rows = db.rows("SELECT * FROM paper_entries WHERE entry_id = ?", [entry_id])
    if not rows:
        raise KeyError(f"no paper entry {entry_id}")
    return _decode(rows[0])


def decisions(entry_id: str) -> list[dict[str, Any]]:
    """Every decision of an entry, oldest first.

    Args:
        entry_id: Entry id.

    Returns:
        Rows ``entry_id, as_of, decided_at, targets`` (dict), ``next_open``, ``inputs_hash``, ``code_version`` and
        ``lead_hours`` (from the decision to the first fill; negative when it was taken after that open).
    """
    rows = db.rows("SELECT * FROM paper_decisions WHERE entry_id = ? ORDER BY as_of", [entry_id])
    for r in rows:
        r["targets"] = json.loads(r["targets"] or "{}")
        r["lead_hours"] = sessions.hours_between(datetime.fromisoformat(r["decided_at"]),
                                                 datetime.fromisoformat(r["next_open"]))
    return rows


def subject_of(item: dict[str, Any]) -> BarSubject | PanelSubject:
    """Load the data and strategy of an entry the way the gauntlet judged it.

    Args:
        item: Entry row (decoded).

    Returns:
        A subject on the full stored history.

    Raises:
        FileNotFoundError: A series isn't ingested.
    """
    meta = item["reference"]["subject"]
    return subject_from_meta(meta, pd.Timestamp(meta["lockbox_from"], tz="UTC"))


def calendar_of(subject: BarSubject | PanelSubject) -> tuple[pd.DatetimeIndex, list[str]]:
    """Session dates and the asset class of each member.

    Args:
        subject: A loaded subject.

    Returns:
        ``(dates at 00:00 UTC, asset classes)``.
    """
    if isinstance(subject, PanelSubject):
        return subject.panel.ts, list(subject.panel.asset_class)
    days = pd.DatetimeIndex(subject.bars["ts"]).tz_convert("UTC").normalize()
    info = SOURCES.get(subject.meta["source"])
    return days, [info.asset_class if info else "equity"]


def targets_at(subject: BarSubject | PanelSubject, params: dict[str, float], upto: np.ndarray) -> tuple[dict, str]:
    """The target a strategy decides at the last close of a clipped history.

    Args:
        subject: A loaded subject.
        params: Strategy parameters.
        upto: Boolean per date of the subject's calendar: the data the decision may see.

    Returns:
        ``(targets, inputs_hash)``: weight per instrument (a position in [-1, 1] for a single instrument) and a
        fingerprint of the data used.
    """
    if isinstance(subject, PanelSubject):
        panel = subject.panel.slice(upto)
        w = subject.strat.weights(panel, params)[-1]
        return {s: float(x) for s, x in zip(panel.symbols, w, strict=True)}, panel_hash(panel)
    bars = subject.bars[upto].reset_index(drop=True)
    p = subject.strat.positions(bars, params)
    last = float(np.clip(np.nan_to_num(float(p.iloc[-1])), -1.0, 1.0)) if len(p) else 0.0
    return {subject.meta["symbol"]: last}, store.data_hash(bars)


def add(card_id: str, note: str = "", actor: str = "user", now: datetime | None = None) -> dict[str, Any]:
    """Put a report card in the book and freeze what its research period expects.

    Args:
        card_id: Gauntlet report card id (any verdict).
        note: Why it is tracked, in plain words.
        actor: Who added it, for the audit log.
        now: Time of the action; None means now.

    Returns:
        The new entry, decoded.

    Raises:
        FileNotFoundError: Unknown card, or its data isn't ingested.
        Refused: The card is already active in the book, or it isn't a daily or portfolio strategy.
    """
    card = load_card(card_id)
    m = card["meta"]
    if m.get("kind") != "portfolio" and m.get("interval") != "1d":
        raise Refused("the book decides once a day after the close, so only daily and portfolio cards can join it")
    if db.scalar("SELECT count(*) FROM paper_entries WHERE card_id = ? AND status = 'active'", [card_id]):
        raise Refused(f"card {card_id} is already in the book")

    entry_id = uuid.uuid4().hex[:12]
    subject_meta = {k: m.get(k) for k in SUBJECT_KEYS}
    subject = subject_from_meta(subject_meta, pd.Timestamp(m["lockbox_from"], tz="UTC"))
    params = m["params"]
    sim = subject.simulate(params)
    ref = monitor.reference_from(sim.net, sim.bench, subject.rf, subject.dpy)
    summary = metrics.summary(pd.Series(sim.net), pd.Series(subject.rf), subject.dpy, benchmark=pd.Series(sim.bench))
    trial_id = ledger.record_trial(
        question=paper_question(m["question"]), family=m["family"], strategy=m["strategy"], params=params,
        symbol=subject.scope, start=subject.research[0], end=subject.research[1], source=PAPER_SOURCE,
        metrics=summary, data_hash=subject.data_hash,
    )
    sig = next((s["chart"] for s in card["stages"] if s["key"] == "significance"), None) or {}
    ref.update({
        "subject": subject_meta,
        "research": list(subject.research),
        "trial_id": trial_id,
        # The card's own research band, the one its lockbox stage would use.
        "card": {k: sig.get(k) for k in ("sr", "skew", "kurt", "rho", "n", "sr_annual")},
    })
    when = naive_utc(now)
    with db.connect() as con:
        con.execute(
            """INSERT INTO paper_entries (entry_id, card_id, kind, question, family, strategy, scope, params, verdict,
                                          added_at, status, note, reference, retired_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL)""",
            [entry_id, card_id, subject.kind, m["question"], m["family"], m["strategy"], subject.scope,
             db.dumps(params), card["verdict"], when, note, db.dumps(db.json_safe(ref))],
        )
    audit.log("paper.add", target=entry_id, actor=actor, reason=note,
              details={"card_id": card_id, "question": m["question"], "verdict": card["verdict"]})
    return entry(entry_id)


def retire(entry_id: str, reason: str, actor: str = "user", now: datetime | None = None) -> dict[str, Any]:
    """Stop an entry: no new decisions, and scoring ends at this time. Its decisions stay.

    Args:
        entry_id: Entry id.
        reason: Why, in plain words (required).
        actor: Who retired it.
        now: Time of the action; None means now.

    Returns:
        The updated entry.

    Raises:
        KeyError: Unknown entry.
        Refused: No reason given, or the entry is already retired.
    """
    item = entry(entry_id)
    if not reason.strip():
        raise Refused("a reason is required to retire an entry")
    if item["status"] != "active":
        raise Refused(f"entry {entry_id} is already {item['status']}")
    with db.connect() as con:
        con.execute("UPDATE paper_entries SET status = 'retired', retired_at = ? WHERE entry_id = ?",
                    [naive_utc(now), entry_id])
    audit.log("paper.retire", target=entry_id, actor=actor, reason=reason, details={"question": item["question"]})
    return entry(entry_id)


def decide(item: dict[str, Any], now: datetime | None = None, as_of: str | None = None) -> dict[str, Any]:
    """Record one entry's target for the latest final close, unless that would see the outcome.

    Args:
        item: Entry row (decoded).
        now: Time of the decision (tz-aware); None means now.
        as_of: Session date to decide on, ``YYYY-MM-DD``. None means the latest final close. Any earlier date is a
            backfill and is refused.

    Returns:
        ``entry_id``, ``question``, ``status`` (``decided``, ``exists`` or ``refused``), ``as_of`` and ``message``;
        a new decision also has ``targets``, ``decided_at``, ``next_open``, ``lead_hours`` and ``late``.
    """
    now = now or now_utc()
    out: dict[str, Any] = {"entry_id": item["entry_id"], "question": item["question"], "as_of": None}

    def refuse(msg: str) -> dict[str, Any]:
        return {**out, "status": "refused", "message": msg}

    if item["status"] != "active":
        return refuse(f"the entry is {item['status']}")
    subject = subject_of(item)
    days, classes = calendar_of(subject)
    final = sessions.final_mask(days, classes, now)
    if not final.any():
        return refuse("no bar has a final close yet")
    latest = days[final][-1]
    day = latest if as_of is None else pd.Timestamp(as_of, tz="UTC")
    out["as_of"] = str(day.date())
    if day not in days[final]:
        return refuse(f"there is no final close on {day.date()}")
    if day < latest:
        return refuse(f"backfill: the bar of {latest.date()} is already stored, so a decision on {day.date()} now "
                      "would be taken after its outcome was known")
    last = db.scalar("SELECT max(as_of) FROM paper_decisions WHERE entry_id = ?", [item["entry_id"]])
    if last is not None and str(last) == str(day.date()):
        return {**out, "status": "exists", "message": f"already decided on {day.date()}"}
    if last is not None and str(last) > str(day.date()):
        return refuse(f"a decision on {last} already exists; decisions only move forward")
    nxt = sessions.next_session(day, classes)
    if sessions.first_close(nxt, classes) <= now:
        return refuse(f"stale data: the session of {nxt.date()} has already closed but isn't stored; ingest first")

    targets, inputs = targets_at(subject, item["params"], np.asarray(days <= day))
    next_open = sessions.first_open(nxt, classes)
    with db.connect() as con:
        con.execute(
            """INSERT INTO paper_decisions (entry_id, as_of, decided_at, targets, next_open, inputs_hash, code_version)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [item["entry_id"], day.date(), naive_utc(now), db.dumps(targets), naive_utc(next_open), inputs,
             code_version()],
        )
    lead = sessions.hours_between(now, next_open)
    return {**out, "status": "decided", "targets": targets, "decided_at": naive_utc(now).isoformat(),
            "next_open": naive_utc(next_open).isoformat(), "lead_hours": lead, "late": lead < 0,
            "message": f"decided on {day.date()}, fills at the open of {nxt.date()}"
                       + (f" ({-lead:.1f} h after it opened: late)" if lead < 0 else f" in {lead:.1f} h")}


def run(now: datetime | None = None, as_of: str | None = None, actor: str = "user",
        entry_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Decide every active entry for the latest final close.

    Safe to run any number of times a day: an entry already decided on the latest close is skipped. Entries whose
    data is missing are reported with status ``error`` instead of stopping the rest.

    Args:
        now: Time of the decisions (tz-aware); None means now.
        as_of: Session date to decide on; None means the latest final close (see ``decide``).
        actor: Who ran it, for the audit log.
        entry_ids: Only these entries; None means every active one.

    Returns:
        One result per entry (see ``decide``).
    """
    now = now or now_utc()
    items = [e for e in entries("active") if entry_ids is None or e["entry_id"] in entry_ids]
    results = []
    for item in items:
        try:
            results.append(decide(item, now, as_of))
        except (FileNotFoundError, KeyError, ValueError) as err:
            results.append({"entry_id": item["entry_id"], "question": item["question"], "as_of": as_of,
                            "status": "error", "message": str(err)})
    counts = {s: sum(r["status"] == s for r in results) for s in ("decided", "exists", "refused", "error")}
    audit.log("paper.run", target="book", actor=actor,
              details={**counts, "refused": [r["message"] for r in results if r["status"] == "refused"][:5]})
    return results
