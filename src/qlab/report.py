"""The daily lab report: what the lab tested, what passed, what is in paper trading and how it is doing.

Design (decision 0008):

- **Read, don't recompute.** The report is assembled from what the lab already recorded: the ledger (trials since the
  last report), the report cards through the registry (verdicts and lifecycle states), the paper book's saved forward
  reports (results against their frozen bands, the decay alarm), the audit log (paper runs, refusals, the nightly
  job, calibration studies) and the data catalog (freshness). It runs no backtest and scores nothing, so building it
  adds no trial to the ledger.
- **Since the last report.** Each report covers the time from the previous report to now, whichever way either was
  built (nightly job, CLI or web), so nothing is counted twice or missed. The first one covers everything.
- **The plain answer first.** ``headline`` is a short list of sentences, each with a tone, that answers the owner's
  daily question without opening a chart. The sections behind it hold the numbers.
- **Alerts come from the difference between two reports** (``alerts.evaluate``): each report stores the lifecycle
  state of every card and the alert conditions it saw, and the next one compares.
- Stored as ``reports/daily/<id>.json`` with a catalog row in ``daily_reports``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from . import alerts, audit, config, db, registry
from .data import freshness
from .timeutil import naive_utc, now_utc

NON_RESEARCH = ("calibration", "paper", "demo")
MAX_QUESTIONS = 40
MAX_ENTRY_LINES = 5
CARD_KEYS = ("card_id", "created_at", "question", "family", "strategy", "scope", "verdict", "state", "label",
             "near_miss", "failed", "since", "entry_id", "interval", "edge", "oos_edge", "perm_p", "dsr")


def _events(action: str, since: str | None) -> list[dict[str, Any]]:
    """Audit events of one kind after a time, oldest first.

    Args:
        action: Audit action, e.g. ``"paper.run"``.
        since: ISO time; None means all.

    Returns:
        Event rows with ``details`` decoded.
    """
    where, args = ("AND ts > ?", [since]) if since else ("", [])
    rows = db.rows(f"SELECT * FROM audit WHERE action = ? {where} ORDER BY id", [action, *args])
    for r in rows:
        r["details"] = json.loads(r["details"] or "{}")
    return rows


def trials_since(since: str | None) -> dict[str, Any]:
    """Trials recorded after a time, by origin and by research question.

    Args:
        since: ISO time; None means since the lab began.

    Returns:
        ``total``, ``research`` (all origins except calibration, paper and demo), ``calibration``, ``paper``,
        ``questions`` (distinct research questions), ``by_source`` (rows ``source, n, questions``), ``by_question``
        (the busiest research questions: ``question, source, n, n_total`` (every trial ever on the question: the N its
        statistics are deflated by), ``best_edge_sharpe``) and ``calibration_studies`` (studies finished since).
    """
    where, args = ("WHERE created_at > ?", [since]) if since else ("", [])
    by_source = db.rows(f"""SELECT source, count(*) AS n, count(DISTINCT question) AS questions FROM trials {where}
                            GROUP BY source ORDER BY n DESC""", args)
    skip = ", ".join(f"'{s}'" for s in NON_RESEARCH)
    research_where = f"{where} {'AND' if where else 'WHERE'} source NOT IN ({skip})"
    by_question = db.rows(
        f"""SELECT q.question, q.source, q.n, q.best_edge_sharpe, t.n_total FROM
              (SELECT question, source, count(*) AS n, max(edge_sharpe) AS best_edge_sharpe FROM trials
               {research_where} GROUP BY question, source) q
            JOIN (SELECT question, count(*) AS n_total FROM trials GROUP BY question) t USING (question)
            ORDER BY q.n DESC, q.question LIMIT {MAX_QUESTIONS}""", args)
    n = {r["source"]: int(r["n"]) for r in by_source}
    questions = db.scalar(f"SELECT count(DISTINCT question) FROM trials {research_where}", args)
    studies = [{"report": e["target"], "ts": e["ts"], **e["details"]} for e in _events("gauntlet.calibrate", since)]
    return {
        "total": sum(n.values()),
        "research": sum(v for k, v in n.items() if k not in NON_RESEARCH),
        "calibration": n.get("calibration", 0),
        "paper": n.get("paper", 0),
        "questions": int(questions or 0),
        "by_source": by_source,
        "by_question": by_question,
        "calibration_studies": studies,
    }


def paper_since(since: str | None) -> dict[str, Any]:
    """The paper book from its saved forward reports, and the decisions taken since a time.

    Args:
        since: ISO time; None means all.

    Returns:
        ``entries`` (one row per entry: ids, question, verdict, status, ``scored_at``, ``through``, ``n_days``,
        ``forward_return``, ``benchmark_return``, ``band`` (research, forward, floor, ceiling, status,
        evidence_years), ``alarm`` (applicable, value, h, alarm, first_alarm, arl1_years), ``n_decisions``, ``late``,
        ``last_decision`` and ``new_decisions``) and ``runs`` (decided, refused and failed decisions since, with the
        refusal messages).
    """
    from .paper import book, score  # the paper book loads the gauntlet; imported here to keep this module light

    entries = []
    for e in book.entries():
        rep = score.load_report(e["entry_id"]) or {}
        decs = book.decisions(e["entry_id"])
        stats = rep.get("stats") or {}
        band = rep.get("band") or {}
        alarm = rep.get("alarm") or {}
        last = decs[-1] if decs else None
        entries.append({
            **{k: e[k] for k in ("entry_id", "card_id", "question", "family", "strategy", "scope", "verdict", "status",
                                 "added_at", "retired_at")},
            "research_edge_sharpe": e["reference"].get("edge_sharpe"),
            "scored_at": rep.get("scored_at"),
            "through": rep.get("through"),
            "n_days": rep.get("n_days", 0),
            "forward_return": (stats.get("strategy") or {}).get("total_return"),
            "benchmark_return": (stats.get("benchmark") or {}).get("total_return"),
            "band": {k: band.get(k) for k in ("research", "forward", "floor", "ceiling", "status", "evidence_years")},
            "alarm": {"applicable": alarm.get("applicable"), "value": alarm.get("value"), "h": alarm.get("h"),
                      "alarm": bool(alarm.get("alarm")), "first_alarm": alarm.get("first_alarm"),
                      "arl1_years": (alarm["arl1_days"] / 252) if alarm.get("arl1_days") else None} if alarm else None,
            "n_decisions": len(decs),
            "late": sum(d["lead_hours"] < 0 for d in decs),
            "last_decision": {k: last[k] for k in ("as_of", "decided_at", "next_open", "lead_hours")} if last else None,
            "new_decisions": sum(since is None or d["decided_at"] > since for d in decs),
        })
    runs = _events("paper.run", since)
    return {
        "entries": entries,
        "runs": {
            "n": len(runs),
            "decided": sum(int(r["details"].get("decided", 0)) for r in runs),
            "refused": sum(len(r["details"].get("refused", [])) for r in runs),
            "errors": sum(int(r["details"].get("error", 0)) for r in runs),
            "messages": [m for r in runs for m in r["details"].get("refused", [])][-5:],
        },
    }


def last_nightly() -> dict[str, Any] | None:
    """The latest nightly job the audit log knows about.

    Returns:
        ``ts`` (when its report was built, UTC), ``ok``, ``mode`` and ``summary`` (the job's summary); None if the job
        never reported.
    """
    rows = db.rows("SELECT ts, details FROM audit WHERE action = 'paper.nightly' ORDER BY id DESC LIMIT 1")
    if not rows:
        return None
    d = json.loads(rows[0]["details"] or "{}")
    return {"ts": rows[0]["ts"], "ok": bool(d.get("ok")), "mode": d.get("mode"), "summary": d}


def _since_text(since: str | None) -> str:
    """How a report names its period.

    Args:
        since: ISO time of the previous report, or None.

    Returns:
        ``"since the last report (YYYY-MM-DD HH:MM UTC)"`` or ``"since the lab began"``.
    """
    return f"since the last report ({since[:16].replace('T', ' ')} UTC)" if since else "since the lab began"


def _pct(x: float | None) -> str:
    """Format a signed percentage for a sentence.

    Args:
        x: Fraction.

    Returns:
        E.g. ``+1.23%``, or a dash.
    """
    return "–" if x is None else f"{x:+.2%}"


def _plural(n: int, word: str, many: str | None = None) -> str:
    """A count with its noun.

    Args:
        n: Count.
        word: Singular noun.
        many: Plural noun; None adds an ``s``.

    Returns:
        E.g. ``"1 card"``, ``"3 cards"``.
    """
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def headline(rep: dict[str, Any]) -> list[dict[str, str]]:
    """The plain answer: one sentence per question the owner asks every day.

    Args:
        rep: A report with its sections filled in (``trials``, ``verdicts``, ``transitions``, ``registry``, ``paper``,
            ``freshness``, ``nightly``).

    Returns:
        Items ``{"topic", "tone", "text"}``; tone is ``ok``, ``good``, ``info``, ``warn`` or ``bad``.
    """
    out: list[dict[str, str]] = []
    t, since = rep["trials"], _since_text(rep["since"])

    if t["total"]:
        extra = [f"{t['calibration']:,} calibration trials" if t["calibration"] else "",
                 f"{t['paper']:,} paper-book scorings" if t["paper"] else ""]
        extra = [x for x in extra if x]
        text = (f"Tested {_plural(t['research'], 'research trial')} on {_plural(t['questions'], 'question')} {since}"
                + (f", plus {' and '.join(extra)}" if extra else "") + ".")
    else:
        text = f"No new trials {since}."
    out.append({"topic": "tested", "tone": "info", "text": text})

    v = rep["verdicts"]
    if v:
        by = {s: [c for c in v if c["state"] == s or (s == "near_miss" and c["near_miss"])] for s in registry.STATES}
        passed = [c for c in v if c["verdict"] in ("candidate", "validated")]
        parts = [_plural(len(passed), "candidate"), _plural(len(by["near_miss"]), "near-miss", "near-misses"),
                 f"{sum(c['verdict'] == 'rejected' and not c['near_miss'] for c in v):,} rejected"]
        names = [c["question"] for c in passed] + [f"{c['question']} (near-miss)" for c in by["near_miss"]]
        text = f"{_plural(len(v), 'new report card')}: {', '.join(parts)}" + (f": {', '.join(names[:4])}" if names
                                                                           else "") + "."
        out.append({"topic": "verdicts", "tone": "good" if passed else "info", "text": text})
    else:
        out.append({"topic": "verdicts", "tone": "info", "text": "No new verdicts."})
    moved = [m for m in rep["transitions"] if m["from"] is not None]
    if moved:
        text = "; ".join(f"{m['question']} moved from {registry.LABELS[m['from']].lower()} to "
                         f"{registry.LABELS[m['to']].lower()}" for m in moved[:4]) + "."
        out.append({"topic": "moves", "tone": "info", "text": text})

    c = rep["registry"]["counts"]
    n_cards = sum(c.values())
    text = (f"Registry: {c['validated']} validated, {_plural(c['candidate'], 'candidate')}, "
            f"{c['in_paper_book']} in the paper book, {_plural(c['near_miss'], 'near-miss', 'near-misses')}, "
            f"{c['rejected']} rejected, "
            f"{c['retired']} retired ({_plural(n_cards, 'card')}).")
    if not (c["validated"] or c["candidate"]) and n_cards:
        text += " Nothing has passed the gauntlet yet."
    out.append({"topic": "registry", "tone": "good" if c["validated"] or c["candidate"] else "info", "text": text})

    active = [e for e in rep["paper"]["entries"] if e["status"] == "active"]
    if not active:
        out.append({"topic": "paper", "tone": "info", "text": "The paper book is empty."})
    else:
        alarms = [e for e in active if (e["alarm"] or {}).get("alarm")]
        below = [e for e in active if e["band"]["status"] == "below"]
        scored = [e for e in active if e["n_days"]]
        days = max((e["n_days"] for e in active), default=0)
        through = max((e["through"] for e in scored if e["through"]), default=None)
        state = ("; ".join(x for x in [f"{len(alarms)} decay alarm{'s' if len(alarms) != 1 else ''}" if alarms else "",
                                       f"{len(below)} below band" if below else ""] if x)
                 or ("all inside their bands, no decay alarm" if scored else "no forward day scored yet"))
        text = (f"Paper book: {_plural(len(active), 'active entry', 'active entries')}"
                + (f", up to {_plural(days, 'forward day')} through {through}" if through else "") + f": {state}.")
        tone = "bad" if alarms else "warn" if below else "ok" if scored else "info"
        out.append({"topic": "paper", "tone": tone, "text": text})
        for e in active[:MAX_ENTRY_LINES]:
            if e["n_days"]:
                a = e["alarm"] or {}
                alarm = ("alarm n/a" if not a.get("applicable") else
                         f"alarm {a['value']:.1f} of {a['h']:.1f}" if a.get("value") is not None else "alarm –")
                band = e["band"]["status"] or "no band yet"
                text = (f"{e['question']} ({e['verdict']}): {_pct(e['forward_return'])} over "
                        f"{_plural(e['n_days'], 'day')} vs {_pct(e['benchmark_return'])} for its benchmark; edge "
                        f"{band}{' its band' if band in ('inside', 'below', 'above') else ''}; {alarm}.")
            else:
                last = e["last_decision"]
                text = (f"{e['question']} ({e['verdict']}): " + (f"last decision on the {last['as_of']} close, no "
                        "forward day scored yet." if last else "no decision yet."))
            tone = "bad" if (e["alarm"] or {}).get("alarm") else "warn" if e["band"]["status"] == "below" else "info"
            out.append({"topic": "paper_entry", "tone": tone, "text": text})
        if len(active) > MAX_ENTRY_LINES:
            out.append({"topic": "paper_entry", "tone": "info",
                        "text": f"…and {len(active) - MAX_ENTRY_LINES} more entries (Trading Desk)."})
    runs = rep["paper"]["runs"]
    if runs["refused"] or runs["errors"]:
        out.append({"topic": "paper_runs", "tone": "warn",
                    "text": f"{runs['refused'] + runs['errors']} paper decisions were refused or failed {since}"
                            + (f": {runs['messages'][-1]}" if runs["messages"] else "") + "."})

    stale = [f for f in rep["freshness"] if f["stale"]]
    if stale:
        out.append({"topic": "data", "tone": "warn", "text": "Stale data: " + " ".join(f["message"] for f in stale)})
    else:
        daily = [f for f in rep["freshness"] if f["interval"] == "1d"]
        out.append({"topic": "data", "tone": "ok",
                    "text": "Data is fresh: " + ", ".join(f"{f['source']} through {f['oldest']}" for f in daily) + "."})

    n = rep["nightly"]
    this, last = n.get("this_run"), n.get("last")
    if this is not None:
        out.append({"topic": "nightly", "tone": "ok" if this.get("ok") else "bad",
                    "text": "Nightly job: every step ran cleanly." if this.get("ok") else
                    "Nightly job: finished with problems (see the alert)."})
    elif last is None:
        out.append({"topic": "nightly", "tone": "warn",
                    "text": "The nightly job hasn't reported yet: run make paper-nightly after the close, or install "
                            "it with make paper-launchd."})
    else:
        age = (datetime.fromisoformat(rep["created_at"]) - datetime.fromisoformat(last["ts"])).total_seconds() / 3600
        tone = "bad" if not last["ok"] else "warn" if age > 36 else "ok"
        out.append({"topic": "nightly", "tone": tone,
                    "text": f"Last nightly job {last['ts'][:16].replace('T', ' ')} UTC ({age:.0f} h ago): "
                            + ("ok." if last["ok"] else "finished with problems.")})
    return out


def _previous() -> dict[str, Any] | None:
    """The latest stored report's catalog row.

    Returns:
        ``report_id, created_at, states`` (dict) and ``conditions`` (set), or None before the first report.
    """
    rows = db.rows("SELECT report_id, created_at, states, conditions FROM daily_reports ORDER BY created_at DESC "
                   "LIMIT 1")
    if not rows:
        return None
    r = rows[0]
    return {**r, "states": json.loads(r["states"] or "{}"), "conditions": set(json.loads(r["conditions"] or "[]"))}


def report_path(report_id: str):
    """Where a report is saved.

    Args:
        report_id: Report id.

    Returns:
        ``reports/daily/<report_id>.json``.
    """
    return config.reports_dir() / "daily" / f"{report_id}.json"


def digest(rep: dict[str, Any]) -> alerts.Alert:
    """The report's headline as one message, for the channels.

    Args:
        rep: A built report.

    Returns:
        A ``daily_report`` alert whose severity is the worst tone in the headline.
    """
    tones = {h["tone"] for h in rep["headline"]}
    severity = "bad" if "bad" in tones else "warn" if "warn" in tones else "info"
    return alerts.Alert("daily_report", severity, f"report:{rep['report_id']}",
                        f"Lab report {rep['created_at'][:10]}", "\n".join(f"• {h['text']}" for h in rep["headline"]),
                        "/board")


def build(now: datetime | None = None, trigger: str = "cli", nightly: dict[str, Any] | None = None,
          notify: bool = True, send_digest: bool = False, actor: str = "user") -> dict[str, Any]:
    """Build today's report, save it, and raise the alerts it implies.

    Args:
        now: Time of the report (tz-aware); None means now.
        trigger: ``nightly``, ``cli`` or ``web``.
        nightly: Summary of the nightly job that asked for this report, or None. Recorded in the audit log, and a
            failed job raises an alert.
        notify: False delivers alerts to the file channel only.
        send_digest: Also send the headline as one message through the channels.
        actor: Who asked, for the audit log.

    Returns:
        The report: ``report_id, created_at, since, previous_id, trigger, headline, trials, verdicts, transitions,
        registry`` (``counts``, ``cards``), ``paper``, ``freshness``, ``nightly`` (``this_run``, ``last``),
        ``alerts`` (raised by this report, with delivery per channel) and ``conditions``.
    """
    now = now or now_utc()
    prev = _previous()
    since = prev["created_at"] if prev else None
    report_id = uuid.uuid4().hex[:12]
    created = naive_utc(now)
    if nightly is not None:
        audit.log("paper.nightly", target=report_id, actor="nightly", details=nightly)

    items = registry.cards()
    states = registry.snapshot(items)
    cards = [{k: c[k] for k in CARD_KEYS} for c in items]
    transitions = []
    if prev is not None:
        for c in cards:
            before = prev["states"].get(c["card_id"])
            if before != c["state"]:
                transitions.append({"card_id": c["card_id"], "question": c["question"], "from": before,
                                    "to": c["state"]})
    rep: dict[str, Any] = {
        "report_id": report_id,
        "created_at": created.isoformat(timespec="seconds"),
        "since": since,
        "previous_id": prev["report_id"] if prev else None,
        "trigger": trigger,
        "trials": trials_since(since),
        "verdicts": [c for c in cards if since is None or c["created_at"] > since],
        "transitions": transitions,
        "registry": {"counts": registry.counts(items), "cards": cards},
        "paper": paper_since(since),
        "freshness": freshness.check(now),
        "nightly": {"this_run": nightly, "last": last_nightly()},
    }
    rep["headline"] = headline(rep)
    raised = alerts.evaluate(rep, prev["conditions"] if prev else None, nightly)
    if raised:
        rep["headline"].append({"topic": "alerts", "tone": "bad" if any(a.severity == "bad" for a in raised) else
                                "warn" if any(a.severity == "warn" for a in raised) else "good",
                                "text": f"{_plural(len(raised), 'new alert')}: " + "; ".join(a.title for a in raised)
                                        + "."})
    rep["conditions"] = sorted(alerts.conditions(rep))
    if send_digest:
        raised.append(digest(rep))
    rep["alerts"] = alerts.record(raised, alerts.deliver(raised, notify), report_id, now) if raised else []
    rep = db.json_safe(rep)

    path = report_path(report_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep))
    with db.connect() as con:
        con.execute("""INSERT INTO daily_reports (report_id, created_at, since, trigger, headline, n_alerts, states,
                                                  conditions, path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [report_id, created, since, trigger, db.dumps(rep["headline"]), len(rep["alerts"]),
                     db.dumps(states), db.dumps(rep["conditions"]), str(path)])
    audit.log("report.build", target=report_id, actor=actor,
              details={"trigger": trigger, "alerts": [a["rule"] for a in rep["alerts"]],
                       "trials": rep["trials"]["total"], "verdicts": len(rep["verdicts"])})
    return rep


def load(report_id: str) -> dict[str, Any]:
    """A saved report.

    Args:
        report_id: Report id.

    Returns:
        The report.

    Raises:
        FileNotFoundError: Unknown report.
    """
    path = report_path(report_id)
    if not path.exists():
        raise FileNotFoundError(f"no report {report_id}")
    return json.loads(path.read_text())


def latest() -> dict[str, Any] | None:
    """The newest saved report.

    Returns:
        The report, or None before the first one.
    """
    prev = _previous()
    return load(prev["report_id"]) if prev else None


def list_reports(limit: int = 30) -> list[dict[str, Any]]:
    """Saved reports, newest first.

    Args:
        limit: Maximum number of reports.

    Returns:
        Rows ``report_id, created_at, since, trigger, n_alerts`` and ``headline`` (list).
    """
    rows = db.rows("SELECT report_id, created_at, since, trigger, n_alerts, headline FROM daily_reports "
                   "ORDER BY created_at DESC LIMIT ?", [limit])
    for r in rows:
        r["headline"] = json.loads(r["headline"] or "[]")
    return rows


def render_text(rep: dict[str, Any]) -> str:
    """The report as plain text for a terminal.

    Args:
        rep: A report.

    Returns:
        The headline, the paper book, freshness and the alerts raised, one item per line.
    """
    marks = {"ok": "ok  ", "good": "++  ", "info": "    ", "warn": "!!  ", "bad": "XX  "}
    lines = [f"Lab report {rep['report_id']}, {rep['created_at'].replace('T', ' ')} UTC, {_since_text(rep['since'])}",
             ""]
    lines += [f"  {marks.get(h['tone'], '    ')}{h['text']}" for h in rep["headline"]]
    if rep["trials"]["by_question"]:
        lines += ["", "  Busiest research questions (trials since, N on the question):"]
        lines += [f"    {q['question']:<40} {q['source']:<9} {q['n']:>7,}  N = {q['n_total']:,}"
                  for q in rep["trials"]["by_question"][:8]]
    lines += ["", "  Data freshness:"] + [f"    {'STALE' if f['stale'] else 'ok   '} {f['message']}"
                                          for f in rep["freshness"]]
    if rep["alerts"]:
        lines += ["", "  Alerts raised:"] + [
            f"    [{a['severity']}] {a['title']}  ({', '.join(f'{k} {v}' for k, v in a['delivery'].items())})"
            for a in rep["alerts"]]
    lines += ["", f"  Saved to {report_path(rep['report_id'])}"]
    return "\n".join(lines)
