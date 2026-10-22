"""What the Radar page, the API, the bot and the report read: the latest scan, anomalies, scenarios and charts.

Everything here reads what the collectors and the scan stored; nothing fetches, scans or writes, so a page load can't
add load on a source or a trial to the ledger.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .. import alerts, db
from ..timeutil import naive_utc, now_utc
from . import detect, scan, scenarios, sentiment, signals, store, watchlist

NOTE = ("A risk-and-awareness tool first. Attention and news sentiment mostly predict volatility and trading volume, "
        "weakly predict returns, and what they predict fades once it is known. Responses only ever reduce risk; any "
        "trading use must pass the gauntlet.")
CHART_DAYS = 365
TONE_DAYS = 180


def _signal_rows(scan_out: dict[str, Any] | None) -> list[dict[str, Any]]:
    """One row per signal from the latest scan, with its worst current severity.

    Args:
        scan_out: The latest scan (``scan.latest_scan``) or None.

    Returns:
        ``signal, label, category, ts, value, unit, stale, severity, detectors`` (severity per detector), sorted with
        severe first.
    """
    by: dict[str, dict[str, Any]] = {}
    rank = {"severe": 2, "notable": 1, None: 0}
    for r in (scan_out or {}).get("readings", []):
        row = by.setdefault(r["signal"], {k: r[k] for k in ("signal", "label", "category", "ts", "value", "unit",
                                                             "stale")} | {"severity": None, "detectors": {}})
        row["detectors"][r["detector"]] = {"score": r["score"], "notable": r["notable"], "severe": r["severe"],
                                           "severity": r["severity"]}
        if rank[r["severity"]] > rank[row["severity"]]:
            row["severity"] = r["severity"]
    return sorted(by.values(), key=lambda r: (-rank[r["severity"]], r["category"], r["label"]))


def tone(days: int = TONE_DAYS, now: datetime | None = None) -> dict[str, Any]:
    """News tone over recent days: GDELT's average tone per query, and the lexicon's net tone of stored headlines.

    Args:
        days: Days back.
        now: Current time.

    Returns:
        ``gdelt`` (per query: ``key, label, points`` with ``ts`` and ``tone``) and ``headlines`` (per day and source:
        ``day, source, n, tone``) and ``lexicon`` (its name).
    """
    t = pd.Timestamp(now or now_utc()).tz_convert("UTC")
    start = t - pd.Timedelta(days=days)
    out = []
    for it in watchlist.load()["gdelt"]:
        try:
            df = store.read("gdelt", store.slug(it["id"]))
        except FileNotFoundError:
            continue
        df = df[df["ts"] >= start]
        out.append({"key": f"gdelt/{store.slug(it['id'])}", "label": it.get("label", it["id"]),
                    "points": [{"ts": r.ts.isoformat(), "tone": None if pd.isna(r.tone) else float(r.tone)}
                               for r in df.itertuples()]})
    rows = db.rows("""SELECT date_trunc('day', published_at) AS day, source, count(*) AS n, sum(pos) AS pos,
                             sum(neg) AS neg FROM radar_headlines WHERE published_at >= ?
                      GROUP BY ALL ORDER BY day""", [naive_utc(start.to_pydatetime())])
    heads = [{"day": r["day"][:10], "source": r["source"], "n": int(r["n"]),
              "tone": sentiment.net_tone([{"pos": r["pos"], "neg": r["neg"]}])} for r in rows]
    return {"gdelt": out, "headlines": heads, "lexicon": sentiment.lexicon()["name"]}


def overview(now: datetime | None = None) -> dict[str, Any]:
    """Everything the Radar page shows on load.

    Args:
        now: Current time (None means now).

    Returns:
        ``note``, ``generated_at``, ``scan`` (latest scan's time, signal count, open counts, coincidence, calibration
        id; None before the first), ``autopause`` (the setting), ``anomalies`` (open), ``recent`` (closed in the last
        seven days), ``signals`` (see ``_signal_rows``), ``calibration`` (id, time, budget, summary), ``sources``
        (status and series per source), ``scenarios`` (the board), ``tone`` and ``headlines`` (latest 30) and
        ``alerts`` (the latest radar alerts).
    """
    now = now or now_utc()
    s = scan.latest_scan()
    cal = scan.latest_calibration()
    cat = store.catalog()
    per_source: dict[str, dict[str, Any]] = {}
    for r in cat:
        p = per_source.setdefault(r["source"], {"series": 0, "last_published": None})
        p["series"] += 1
        if r["last_published"] and (p["last_published"] is None or r["last_published"] > p["last_published"]):
            p["last_published"] = r["last_published"]
    stress = [per_source[s] for s in ("cboe", "fred", "fng") if s in per_source]  # the stress collector's files
    if stress:
        per_source["stress"] = {"series": sum(p["series"] for p in stress),
                                "last_published": max((p["last_published"] for p in stress if p["last_published"]),
                                                      default=None)}
    sources = [{**r, **per_source.get(r["source"], {"series": 0, "last_published": None})} for r in store.sources()]
    week = naive_utc(now - timedelta(days=7))
    recent = [a for a in scan.anomalies("closed", 100) if a["last_seen"] and a["last_seen"] >= week.isoformat()]
    radar_alerts = [a for a in alerts.recent(50) if a["rule"] == "radar"][:10]
    return db.json_safe({
        "note": NOTE,
        "generated_at": naive_utc(now).isoformat(timespec="seconds"),
        "scan": None if s is None else {k: s.get(k) for k in ("run_id", "created_at", "calibration", "n_signals",
                                                               "open", "coincidence", "paused")},
        "autopause": scan.autopause_enabled(),
        "anomalies": scan.anomalies("open"),
        "recent": recent,
        "signals": _signal_rows(s),
        "calibration": None if cal is None else {k: cal.get(k) for k in ("calib_id", "created_at", "budget",
                                                                         "summary")},
        "sources": sources,
        "scenarios": scenarios.board(now),
        "tone": tone(now=now),
        "headlines": store.headlines(30),
        "alerts": radar_alerts,
    })


def signal_view(key: str, days: int = CHART_DAYS * 2) -> dict[str, Any]:
    """One signal's chart: its shown value, what the detectors test, and each detector's statistic and thresholds.

    Args:
        key: Signal key, e.g. ``wiki/bank_run``.
        days: Days of history to return.

    Returns:
        ``key, label, category, unit, describe, direction, window`` and ``points`` (``ts, value, x`` and one field
        per detector with its statistic), and ``thresholds`` (per detector: ``notable``, ``severe``, ``null_rate``).

    Raises:
        KeyError: No such signal is stored.
    """
    sig = next((s for s in signals.build() if s.key == key), None)
    if sig is None:
        raise KeyError(f"no radar signal {key}")
    cal = scan.latest_calibration() or {"rows": {}}
    stats, th = {}, {}
    for d in sig.detectors:
        c = cal["rows"].get(f"{key}|{d}")
        stats[d] = detect.statistic(sig.x.to_numpy(), d, sig.window, sig.direction,
                                    h=c["notable"] if c and d == "cusum" else None)
        if c:
            th[d] = {"notable": c["notable"], "severe": c["severe"], "null_rate": c.get("null_rate"),
                     "null_rate_severe": c.get("null_rate_severe"), "history_rate": c.get("history_rate")}
    keep = sig.x.index >= sig.x.index[-1] - pd.Timedelta(days=days)
    pts = []
    for i in np.flatnonzero(keep):
        p = {"ts": sig.x.index[i].isoformat(), "value": sig.level.iloc[i], "x": sig.x.iloc[i]}
        p.update({d: stats[d][i] for d in stats})
        pts.append(p)
    return db.json_safe({"key": key, "label": sig.label, "category": sig.category, "unit": sig.unit,
                         "describe": sig.describe, "direction": sig.direction, "window": sig.window, "points": pts,
                         "thresholds": th})
