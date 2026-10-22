"""The radar's scan: every detector on every signal, anomaly episodes, the alert rule, and the opt-in pause.

Design (decision 0016):

- **Calibrate, then scan.** ``calibrate`` sets each detector's thresholds on each signal's own history (the last 15
  years at most) for the budget in ``detect`` (notable about once a month, severe about once a year), and measures
  false alarms and delay on null histories. A scan uses the latest calibration and recalibrates when it is a week
  old. A prediction market without 60 days of history uses a stated prior instead: a move of 10 percentage points in
  a day is notable, 20 severe.
- **Episodes, not days.** An anomaly opens when a detector's statistic on the newest value reaches its notable
  threshold, stays open (and may escalate to severe) while it stays there, and closes on the first scan that sees it
  below. A signal whose newest value is more than five days old has no current anomaly.
- **The alert rule ``radar``.** Only severe anomalies alert, each once, in plain words: what moved, how unusual (how
  often readings this extreme came in the series' own history), since when, and the response committed in advance.
  Rate limits: one alert per signal per 24 hours and at most six radar alerts per day; the rest are summarised once.
- **Coincidences.** Three or more signals with severe anomalies within 24 hours, from at least two categories, raise
  one alert a day. With ``QLAB_RADAR_AUTOPAUSE`` on (default off) and at least one of them from market data (prices,
  VIX, credit), the radar pauses new paper orders through ``broker.controls.pause``, once per coincidence: it pauses
  again only when a severe anomaly that opened after its last pause joins, so a resume the owner decided stands. It
  never resumes: that stays the owner's decision, with a reason and the 24-hour wait. Nothing here can add risk.
"""

from __future__ import annotations

import json
import uuid
import zlib
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .. import alerts, audit, config, db
from ..timeutil import naive_utc, now_utc
from . import detect, signals, watchlist

CALIB_YEARS = 15
RECALIBRATE_DAYS = 7
STALE_DAYS = 5
MAX_ALERTS_PER_DAY = 6
SIGNAL_COOLDOWN = timedelta(hours=24)
COINCIDENCE_MIN = 3
COINCIDENCE_WINDOW = timedelta(hours=24)
MARKET_PREFIXES = ("prices/", "cboe/", "fred/")
INVERSION = 1.0  # VIX above VIX3M: the term structure's definition of stress, a fact rather than a tuned threshold


def calibration_dir():
    """Where calibration reports are saved.

    Returns:
        ``<data>/reports/radar``.
    """
    return config.reports_dir() / "radar"


def _months(x: pd.Series) -> float:
    """Calendar months a daily series spans.

    Args:
        x: Series indexed by day.

    Returns:
        Months (30.44 days each).
    """
    return max((x.index[-1] - x.index[0]).days / 30.44, 1e-9)


def _window(x: pd.Series) -> pd.Series:
    """The part of a history that calibration uses: the last ``CALIB_YEARS`` years.

    Args:
        x: Series indexed by day.

    Returns:
        The tail.
    """
    return x[x.index >= x.index[-1] - pd.Timedelta(days=int(365.25 * CALIB_YEARS))]


def _inversion_rate(ratio: pd.Series) -> dict[str, Any]:
    """How often VIX stood above VIX3M in history.

    Args:
        ratio: VIX / VIX3M by day.

    Returns:
        The calibration row of the ``inversion`` rule (threshold 1, not tuned).
    """
    stat = ratio.to_numpy()
    months = _months(ratio)
    return {"notable": INVERSION, "severe": float("inf"),
            "history_rate": len(detect.episodes(stat, INVERSION)) / months, "null_rate": None,
            "null_rate_severe": None, "detect_rate": None, "delay": None, "detect_rate_small": None,
            "delay_small": None, "n_obs": int(len(stat)), "months": months,
            "share_of_days": float(np.mean(stat >= INVERSION))}


def calibrate(sigs: list[signals.Signal] | None = None, log: Callable[[str], None] | None = None,
              actor: str = "cli", now: datetime | None = None, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Thresholds, false-alarm rates and delays for every detector on every signal; saved as the latest calibration.

    Args:
        sigs: Signals (None builds them from what is stored).
        log: Receives one line per signal.
        actor: Who ran it, for the audit log.
        now: Time of the calibration (None means now).
        base: An earlier calibration whose rows are kept for signals not in ``sigs`` (adding new signals without
            recalibrating the rest).

    Returns:
        ``calib_id, created_at, budget, rows`` (``"<signal>|<detector>"`` to its calibration, see
        ``detect.calibrate``, plus ``signal, detector, label, category``) and ``summary`` (per detector: signals,
        median false alarms per month on null histories at the notable and severe thresholds, median detection rate
        and delay for the planted steps).
    """
    say = log or (lambda _m: None)
    now = now or now_utc()
    sigs = signals.build() if sigs is None else sigs
    rows: dict[str, dict[str, Any]] = dict((base or {}).get("rows", {}))
    for s in sigs:
        x = _window(s.x)
        if len(x) < signals.MIN_HISTORY:
            continue
        for d in s.detectors:
            seed = zlib.crc32(f"{s.key}|{d}".encode())
            c = detect.calibrate(x.to_numpy(), d, s.window, s.direction, _months(x), seed=seed)
            rows[f"{s.key}|{d}"] = {"signal": s.key, "detector": d, "label": s.label, "category": s.category, **c}
        if s.key == "cboe/vix:term":
            rows[f"{s.key}|inversion"] = {"signal": s.key, "detector": "inversion", "label": s.label,
                                          "category": s.category, **_inversion_rate(x)}
        say(f"calibrated {s.key}: {len(x)} days")
    summary = {}
    for d in (*detect.DETECTORS, "inversion"):
        rs = [r for r in rows.values() if r["detector"] == d]
        if not rs:
            continue

        def med(k: str, rs: list[dict[str, Any]] = rs) -> float | None:
            vals = [r[k] for r in rs if r.get(k) is not None]
            return float(np.median(vals)) if vals else None

        summary[d] = {"signals": len(rs), "history_rate": med("history_rate"), "null_rate": med("null_rate"),
                      "null_rate_severe": med("null_rate_severe"), "detect_rate": med("detect_rate"),
                      "delay": med("delay"), "detect_rate_small": med("detect_rate_small"),
                      "delay_small": med("delay_small")}
    calib_id = uuid.uuid4().hex[:12]
    budget = {"notable_per_month": detect.NOTABLE_PER_MONTH, "severe_per_month": detect.SEVERE_PER_MONTH,
              "cooldown": detect.COOLDOWN, "null_reps": detect.NULL_REPS, "plant_reps": detect.PLANT_REPS}
    rep = db.json_safe({"calib_id": calib_id, "created_at": naive_utc(now).isoformat(timespec="seconds"),
                        "budget": budget, "summary": summary, "rows": rows})
    path = calibration_dir() / f"calibration-{calib_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep))
    with db.connect() as con:
        con.execute("INSERT INTO radar_calibrations (calib_id, created_at, budget, summary, path) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [calib_id, naive_utc(now), db.dumps(budget), db.dumps(rep["summary"]), str(path)])
    audit.log("radar.calibrate", target=calib_id, actor=actor, details={"rows": len(rows), "summary": rep["summary"]})
    return rep


def latest_calibration() -> dict[str, Any] | None:
    """The newest saved calibration.

    Returns:
        The report (see ``calibrate``), or None before the first.
    """
    rows = db.rows("SELECT path FROM radar_calibrations ORDER BY created_at DESC LIMIT 1")
    if not rows:
        return None
    try:
        with open(rows[0]["path"]) as fh:
            return json.load(fh)
    except OSError:
        return None


def _since(stat: np.ndarray, h: float, index: pd.DatetimeIndex) -> pd.Timestamp:
    """Start of the alarm episode the newest value belongs to.

    Args:
        stat: Statistic per observation.
        h: Notable threshold.
        index: Days of the observations.

    Returns:
        The day the episode began (gaps shorter than the cooldown don't end it).
    """
    above = np.nan_to_num(stat, nan=-np.inf) >= h
    i, quiet = len(above) - 1, 0
    start = i
    while i >= 0:
        if above[i]:
            start, quiet = i, 0
        else:
            quiet += 1
            if quiet >= detect.COOLDOWN:
                break
        i -= 1
    return index[start]


def _per_year(stat: np.ndarray, value: float, years: float) -> float:
    """How often the history held an episode at least as extreme as ``value``.

    Args:
        stat: Statistic over the history (the newest value excluded by the caller if wanted).
        value: Today's statistic.
        years: Years of history.

    Returns:
        Episodes per year.
    """
    return len(detect.episodes(stat, value)) / max(years, 1e-9)


def readings(sigs: list[signals.Signal], cal: dict[str, Any] | None, now: datetime) -> list[dict[str, Any]]:
    """The newest value of every signal against each detector's thresholds.

    Args:
        sigs: Signals.
        cal: The calibration in use (None: only the market prior applies).
        now: Time of the scan (tz-aware).

    Returns:
        One row per signal and detector: ``signal, detector, label, category, direction, ts, value, unit, score,
        notable, severe, severity`` (None, ``notable`` or ``severe``), ``since``, ``per_year`` (how often readings
        this extreme came in history), ``median`` (of the shown value over the baseline window), ``stale`` and
        ``describe``.
    """
    rows = (cal or {}).get("rows", {})
    t = pd.Timestamp(now).tz_convert("UTC")
    out = []
    for s in sigs:
        x = s.x
        stale = (t - x.index[-1]) > pd.Timedelta(days=STALE_DAYS)
        base = s.level.iloc[-s.window - 1:-1]
        common = {"signal": s.key, "label": s.label, "category": s.category, "direction": s.direction,
                  "ts": x.index[-1].isoformat(), "value": float(s.level.iloc[-1]) if pd.notna(s.level.iloc[-1])
                  else None, "unit": s.unit, "stale": bool(stale), "describe": s.describe,
                  "median": float(base.median()) if len(base) else None}
        years = _months(_window(x)) / 12
        dets = list(s.detectors) + (["inversion"] if s.key == "cboe/vix:term" else [])
        for d in dets:
            c = rows.get(f"{s.key}|{d}")
            if d == "inversion":
                stat = x.to_numpy()
                c = c or {"notable": INVERSION, "severe": float("inf")}
            elif c is None and s.key.startswith("markets/"):
                d, stat = "jump", np.abs(x.to_numpy())
                c = signals.MARKET_JUMP
            elif c is None:
                continue
            else:
                stat = detect.statistic(x.to_numpy(), d, s.window, s.direction,
                                        h=c["notable"] if d == "cusum" else None)
            score = float(stat[-1]) if np.isfinite(stat[-1]) else None
            sev = None
            if score is not None and not stale:
                sev = "severe" if score >= c["severe"] else "notable" if score >= c["notable"] else None
            out.append({**common, "detector": d, "score": score, "notable": c["notable"], "severe": c["severe"],
                        "severity": sev,
                        "since": _since(stat, c["notable"], x.index).isoformat() if sev else None,
                        "per_year": _per_year(stat[:-1], score, years) if sev and d not in ("jump",) else None})
    return out


def _open() -> dict[str, dict[str, Any]]:
    """Open anomaly episodes by ``signal|detector``.

    Returns:
        Rows of ``radar_anomalies``.
    """
    return {f"{r['signal']}|{r['detector']}": r for r in db.rows("SELECT * FROM radar_anomalies WHERE status = 'open'")}


def describe(r: dict[str, Any]) -> dict[str, str]:
    """An anomaly in plain words.

    Args:
        r: A reading with a severity (see ``readings``).

    Returns:
        ``title`` and ``message``: what moved, how unusual, since when and the pre-committed response.
    """
    day = r["ts"][:10]
    side = {"up": "above", "down": "below"}.get(r["direction"], "away from")
    val = _fmt(r["value"], r["unit"])
    med = _fmt(r["median"], r["unit"])
    what = {
        "robust_z": f"{r['label']} was {val} on {day}, {abs(r['score']):.1f} robust standard deviations {side} its "
                    f"usual level (median {med}).",
        "cusum": f"{r['label']} has stayed {side} its usual level (median {med}) long enough to add up: its CUSUM "
                 f"is {r['score']:.1f} against an alarm level of {r['notable']:.1f}; latest {val} on {day}.",
        "bocpd": f"{r['label']} looks like it changed regime: the probability that its current regime began in the "
                 f"last {detect.RECENT} observations is {r['score']:.0%}; latest {val} on {day}.",
        "inversion": f"VIX is above VIX3M (ratio {r['score']:.2f} on {day}): markets price more volatility in the next "
                     "30 days than in the next three months, the term structure of stress.",
        "jump": f"{r['label']} moved {r['score'] * 100:.0f} percentage points in a day, to {val} on {day}.",
    }[r["detector"]]
    rate = r.get("per_year")
    if r["detector"] == "jump":
        unusual = "There is too little history to say how unusual; the fixed rule is 10 points notable, 20 severe."
    elif r["detector"] == "inversion":
        unusual = "Inversions are rare outside sell-offs (see the calibration for how often they came)."
    elif rate is None:
        unusual = ""
    elif rate < 1 / 12:
        unusual = "Nothing this extreme in its history before."
    elif rate < 1:
        unusual = f"Readings this extreme came about once every {1 / rate:.0f} years in its history."
    else:
        unusual = f"Readings this extreme came about {rate:.0f} times a year in its history."
    since = r["since"][:10] if r.get("since") else day
    word = {"up": "rose", "down": "fell"}.get(r["direction"], "moved")
    title = f"Radar ({r['severity']}): {r['label']} {word}"
    msg = (f"What moved: {what}\nHow unusual: {unusual}\nSince: {since}.\n"
           f"Response (committed in advance): {watchlist.response(r['category'])}")
    return {"title": title, "message": msg}


def _fmt(v: float | None, unit: str) -> str:
    """A value in its unit, compactly.

    Args:
        v: Value or None.
        unit: Unit name.

    Returns:
        Text.
    """
    if v is None or not np.isfinite(v):
        return "–"
    if unit == "probability":
        return f"{v:.0%}"
    if unit in ("views/day", "stories/day"):
        return f"{v:,.0f}"
    if unit == "log return":
        return f"{v * 100:+.2f}%"
    if unit == "annualised":
        return f"{v:.0%}"
    return f"{v:,.2f}"


def record(rows: list[dict[str, Any]], now: datetime) -> dict[str, list[dict[str, Any]]]:
    """Open, update and close anomaly episodes from a scan's readings.

    Args:
        rows: Output of ``readings``.
        now: Time of the scan.

    Returns:
        ``opened``, ``updated`` and ``closed`` anomaly rows.
    """
    when = naive_utc(now)
    current = {f"{r['signal']}|{r['detector']}": r for r in rows if r["severity"]}
    seen = {f"{r['signal']}|{r['detector']}" for r in rows}
    opened, updated, closed = [], [], []
    with db.connect() as con:
        for key, a in _open().items():
            if key in current:
                r = current[key]
                sev = "severe" if "severe" in (r["severity"], a["severity"]) else "notable"
                con.execute("""UPDATE radar_anomalies SET ts = ?, value = ?, score = ?, severity = ?, last_seen = ?,
                               message = ? WHERE anomaly_id = ?""",
                            [naive_utc(pd.Timestamp(r["ts"]).to_pydatetime()), r["value"], r["score"], sev, when,
                             describe({**r, "severity": sev})["message"], a["anomaly_id"]])
                updated.append({**a, "severity": sev, "score": r["score"]})
            elif key in seen or a["signal"] not in {r["signal"] for r in rows}:
                con.execute("UPDATE radar_anomalies SET status = 'closed', last_seen = ? WHERE anomaly_id = ?",
                            [when, a["anomaly_id"]])
                closed.append(a)
        known = _open_keys(con)
        for key, r in current.items():
            if key in known:
                continue
            aid = uuid.uuid4().hex[:12]
            con.execute("""INSERT INTO radar_anomalies (anomaly_id, signal, detector, label, category, started, ts,
                           value, score, threshold, severity, direction, status, first_seen, last_seen, alerted_at,
                           message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, NULL, ?)""",
                        [aid, r["signal"], r["detector"], r["label"], r["category"],
                         naive_utc(pd.Timestamp(r["since"]).to_pydatetime()),
                         naive_utc(pd.Timestamp(r["ts"]).to_pydatetime()), r["value"], r["score"], r["notable"],
                         r["severity"], r["direction"], when, when, describe(r)["message"]])
            opened.append({**r, "anomaly_id": aid})
    return {"opened": opened, "updated": updated, "closed": closed}


def _open_keys(con) -> set[str]:
    """Keys of the open episodes, read on an open connection.

    Args:
        con: DuckDB connection.

    Returns:
        ``signal|detector`` keys.
    """
    return {f"{s}|{d}" for s, d in con.execute(
        "SELECT signal, detector FROM radar_anomalies WHERE status = 'open'").fetchall()}


def autopause_enabled() -> bool:
    """Whether the radar may pause new paper orders on a coincidence of severe anomalies.

    Returns:
        True when ``QLAB_RADAR_AUTOPAUSE`` is ``1``, ``true``, ``yes`` or ``on``. Off by default.
    """
    return (config.setting("QLAB_RADAR_AUTOPAUSE") or "").strip().lower() in ("1", "true", "yes", "on")


def coincidence(now: datetime) -> dict[str, Any]:
    """Severe anomalies seen within the last 24 hours, and whether they amount to a coincidence.

    Args:
        now: Time of the check.

    Returns:
        ``active`` (three or more signals from two or more categories), ``signals`` (their rows, one per signal),
        ``categories``, ``market`` (whether one comes from market data).
    """
    since = naive_utc(now - COINCIDENCE_WINDOW)
    rows = db.rows("""SELECT * FROM radar_anomalies WHERE severity = 'severe' AND last_seen >= ?
                      ORDER BY score DESC""", [since])
    by_signal: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_signal.setdefault(r["signal"], r)
    cats = sorted({r["category"] for r in by_signal.values()})
    market = any(s.startswith(MARKET_PREFIXES) for s in by_signal)
    return {"active": len(by_signal) >= COINCIDENCE_MIN and len(cats) >= 2, "signals": list(by_signal.values()),
            "categories": cats, "market": market}


def _recent_alert_keys(now: datetime) -> list[str]:
    """Keys of radar alerts sent in the last 24 hours.

    Args:
        now: Current time.

    Returns:
        Alert keys.
    """
    return [r["key"] for r in db.rows("SELECT key FROM alerts WHERE rule = 'radar' AND created_at >= ?",
                                      [naive_utc(now - timedelta(hours=24))])]


def alert(now: datetime, notify: bool = True, actor: str = "radar") -> dict[str, Any]:
    """Raise the radar's alerts for severe anomalies not yet alerted, and act on a coincidence.

    Args:
        now: Time of the scan.
        notify: False sends to the file channel only.
        actor: Who ran the scan.

    Returns:
        ``alerts`` (stored alert rows), ``coincidence`` (see ``coincidence``) and ``paused`` (the pause control row
        when the radar paused new paper orders, else None).
    """
    sent = _recent_alert_keys(now)
    budget = MAX_ALERTS_PER_DAY - len(sent)
    pending = db.rows("""SELECT * FROM radar_anomalies WHERE status = 'open' AND severity = 'severe'
                         AND alerted_at IS NULL ORDER BY score DESC""")
    items: list[alerts.Alert] = []
    alerted, overflow = [], []
    for a in pending:
        if any(k.startswith(f"radar:{a['signal']}|") for k in sent):
            continue  # this signal alerted within 24 hours; the anomaly waits for the next window
        if budget <= 0:
            overflow.append(a)
            continue
        items.append(alerts.Alert("radar", "warn", f"radar:{a['signal']}|{a['detector']}:{a['anomaly_id']}",
                                  f"Radar: {_label(a)}", a["message"], "/radar"))
        alerted.append(a["anomaly_id"])
        sent.append(items[-1].key)
        budget -= 1
    day = pd.Timestamp(now).tz_convert("UTC").strftime("%Y-%m-%d")
    if overflow and not any(k.startswith(f"radar:overflow:{day}") for k in sent):
        items.append(alerts.Alert("radar", "warn", f"radar:overflow:{day}",
                                  f"Radar: {len(overflow)} more severe anomalies",
                                  "The radar's daily alert limit is reached. Also severe now: "
                                  + "; ".join(_label(a) for a in overflow[:8]) + ". See the Radar page.", "/radar"))
        alerted += [a["anomaly_id"] for a in overflow]
    co = coincidence(now)
    paused = None
    if co["active"] and not any(k == f"radar:coincidence:{day}" for k in sent):
        names = "; ".join(f"{_label(a)} ({a['category']})" for a in co["signals"][:8])
        auto = autopause_enabled()
        text = (f"{len(co['signals'])} signals from {len(co['categories'])} categories have severe anomalies within "
                f"24 hours: {names}. ")
        if auto and co["market"]:
            from ..broker import controls  # the radar only ever reduces risk: pause, never resume

            last = db.scalar("SELECT max(ts) FROM broker_controls WHERE actor = 'radar' AND action = 'pause'")
            fresh = last is None or any(pd.Timestamp(a["first_seen"]) > pd.Timestamp(last) for a in co["signals"])
            if controls.state(now)["mode"] == "active" and fresh:
                paused = controls.pause("all", f"radar: {len(co['signals'])} severe anomalies at once ({names})"[:480],
                                        actor="radar", now=now)
                text += ("New paper orders are paused; positions are kept. Resuming is your decision, on the web "
                         "app, with a reason and a 24-hour wait.")
            elif not fresh:
                text += ("The radar already paused for these anomalies; it doesn't pause again until a new one "
                         "joins them, so a resume you decided stands.")
            else:
                text += "New paper orders were already paused or stopped."
        elif auto:
            text += "None comes from market data, so new paper orders were not paused."
        else:
            text += ("Automatic pause is off (QLAB_RADAR_AUTOPAUSE). Response (committed in advance): "
                     + watchlist.response("stress"))
        items.append(alerts.Alert("radar", "bad", f"radar:coincidence:{day}", "Radar: several severe anomalies at once",
                                  text, "/radar"))
    stored = alerts.record(items, alerts.deliver(items, notify), None, now) if items else []
    if alerted:
        with db.connect() as con:
            for aid in alerted:
                con.execute("UPDATE radar_anomalies SET alerted_at = ? WHERE anomaly_id = ?", [naive_utc(now), aid])
    if paused is not None:
        audit.log("radar.autopause", target="all", actor=actor,
                  details={"signals": [a["signal"] for a in co["signals"]]})
    return {"alerts": stored, "coincidence": co, "paused": paused}


def _label(a: dict[str, Any]) -> str:
    """An anomaly's signal label.

    Args:
        a: An anomaly row.

    Returns:
        Its label, or the signal key when it has none.
    """
    return a.get("label") or a["signal"]


def run(now: datetime | None = None, recalibrate: bool = False, notify: bool = True,
        log: Callable[[str], None] | None = None, actor: str = "cli") -> dict[str, Any]:
    """One scan: calibrate if needed, read every signal, record episodes, alert.

    Args:
        now: Time of the scan (None means now).
        recalibrate: Calibrate even if the latest calibration is recent.
        notify: False sends alerts to the file channel only.
        log: Receives progress lines.
        actor: Who ran it.

    Returns:
        ``run_id, created_at, calibration`` (id), ``n_signals``, ``readings``, ``open`` (anomaly counts by
        severity), ``opened``, ``closed``, ``alerts``, ``coincidence`` and ``paused``.
    """
    now = now or now_utc()
    sigs = signals.build()
    cal = latest_calibration()
    old = cal is None or (pd.Timestamp(now).tz_convert("UTC").tz_localize(None)
                          - pd.Timestamp(cal["created_at"])) > pd.Timedelta(days=RECALIBRATE_DAYS)
    if recalibrate or old:
        cal = calibrate(sigs, log, actor, now)
    else:
        known = {r["signal"] for r in cal["rows"].values()}
        new = [s for s in sigs if s.key not in known and len(_window(s.x)) >= signals.MIN_HISTORY]
        if new:
            cal = calibrate(new, log, actor, now, base=cal)
    rows = readings(sigs, cal, now)
    rec = record(rows, now)
    out_alerts = alert(now, notify, actor)
    open_rows = db.rows("SELECT severity, count(*) AS n FROM radar_anomalies WHERE status = 'open' GROUP BY severity")
    counts = {r["severity"]: int(r["n"]) for r in open_rows}
    run_id = uuid.uuid4().hex[:12]
    out = db.json_safe({
        "run_id": run_id, "created_at": naive_utc(now).isoformat(timespec="seconds"),
        "calibration": cal["calib_id"] if cal else None, "n_signals": len(sigs), "readings": rows,
        "open": {"severe": counts.get("severe", 0), "notable": counts.get("notable", 0)},
        "opened": [o["signal"] + "|" + o["detector"] for o in rec["opened"]],
        "closed": [c["signal"] + "|" + c["detector"] for c in rec["closed"]],
        "alerts": [a["title"] for a in out_alerts["alerts"]], "coincidence": out_alerts["coincidence"],
        "paused": out_alerts["paused"] is not None,
    })
    with db.connect() as con:
        con.execute("INSERT INTO radar_runs (run_id, started_at, finished_at, kind, ok, summary) "
                    "VALUES (?, ?, ?, 'scan', TRUE, ?)", [run_id, naive_utc(now), naive_utc(), json.dumps(out)])
    audit.log("radar.scan", target=run_id, actor=actor,
              details={"signals": len(sigs), "open": out["open"], "alerts": out["alerts"], "paused": out["paused"]})
    return out


def latest_scan() -> dict[str, Any] | None:
    """The newest scan's summary.

    Returns:
        See ``run``; None before the first scan.
    """
    rows = db.rows("SELECT summary FROM radar_runs WHERE kind = 'scan' ORDER BY started_at DESC LIMIT 1")
    return json.loads(rows[0]["summary"]) if rows else None


def anomalies(status: str | None = "open", limit: int = 200) -> list[dict[str, Any]]:
    """Anomaly episodes, severe first, then by score.

    Args:
        status: ``open``, ``closed`` or None for both.
        limit: Maximum rows.

    Returns:
        Rows of ``radar_anomalies``.
    """
    where, args = ("WHERE status = ?", [status]) if status else ("", [])
    rows = db.rows(f"""SELECT * FROM radar_anomalies {where}
                       ORDER BY status DESC, severity = 'severe' DESC, last_seen DESC, score DESC LIMIT ?""",
                   [*args, limit])
    return rows


def line(now: datetime | None = None) -> dict[str, str]:
    """The radar's line for the daily report.

    Args:
        now: Time of the report.

    Returns:
        ``{"topic": "radar", "tone", "text"}``.
    """
    scan = latest_scan()
    if scan is None:
        return {"topic": "radar", "tone": "info", "text": "Radar: no scan yet (qlab radar collect, then scan)."}
    open_ = anomalies("open", 50)
    severe = [a for a in open_ if a["severity"] == "severe"]
    notable = [a for a in open_ if a["severity"] == "notable"]
    when = scan["created_at"][:16].replace("T", " ")
    if not open_:
        return {"topic": "radar", "tone": "ok", "text": f"Radar: quiet, no anomaly open (last scan {when} UTC)."}
    names = ", ".join(_label(a) for a in (severe or notable)[:3])
    tone = "bad" if scan.get("coincidence", {}).get("active") else "warn" if severe else "info"
    return {"topic": "radar", "tone": tone,
            "text": f"Radar: {len(severe)} severe and {len(notable)} notable anomalies open ({names}); last scan "
                    f"{when} UTC."}
