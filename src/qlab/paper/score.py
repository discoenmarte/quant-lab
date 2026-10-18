"""Score the paper book: replay the stored decisions through the backtest engines on the bars that came after them.

Design:

- **Same engines, same timing.** A decision on date t is filled at the next open and held until the next decision is
  filled, exactly as in a backtest (``portfolio.engine`` for panels, ``backtest.engine`` for single instruments), with
  the same costs and cash rate. A day without a decision keeps the last target.
- **Forward days only.** The replay starts at the first decision's close; that day itself isn't scored. Bars whose
  close isn't final yet are left out, and a retired entry stops at its retirement.
- **Against frozen expectations** (``monitor``): forward equity against the research period's expected band, the
  forward edge Sharpe against the card's research band, and the CUSUM decay alarm.
- **Ledger.** Every scoring that sees new bars is a trial (source ``paper``, question ``paper:<question>``), so the
  forward record is in the ledger like every other backtest without spending a lockbox or raising N.
- Reports are saved under ``reports/paper/<entry_id>.json``; the Trading Desk reads them instead of recomputing.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from .. import audit, config, db, ledger
from ..backtest import metrics
from ..backtest.engine import run as run_bars
from ..data import store
from ..gauntlet import stats
from ..gauntlet.subjects import PanelSubject
from ..portfolio.engine import run_portfolio
from ..portfolio.runner import panel_hash
from ..timeutil import naive_utc, now_utc
from . import book, monitor, sessions

MAX_DECISION_ROWS = 60


def _ann(sr: float, dpy: int) -> float | None:
    """Annualise a daily Sharpe ratio.

    Args:
        sr: Daily Sharpe.
        dpy: Days per year.

    Returns:
        The annual value, or None when undefined.
    """
    return float(sr * math.sqrt(dpy)) if sr is not None and math.isfinite(sr) else None


def replay(item: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """Daily forward results of an entry's decisions.

    Args:
        item: Entry row (decoded).
        now: Time of the scoring (tz-aware); None means now.

    Returns:
        ``days`` (dates of the forward bars), ``net``, ``bench``, ``rf``, ``held`` (gross exposure per day),
        ``costs``, ``dpy`` and ``data_hash``; None when the entry has no decision yet.
    """
    now = now or now_utc()
    decs = book.decisions(item["entry_id"])
    if not decs:
        return None
    subject = book.subject_of(item)
    days, classes = book.calendar_of(subject)
    usable = sessions.final_mask(days, classes, now)
    if item.get("retired_at"):
        stop = datetime.fromisoformat(item["retired_at"]).replace(tzinfo=UTC)
        usable = usable & sessions.final_mask(days, classes, stop)
    rows = days.searchsorted(pd.DatetimeIndex([pd.Timestamp(d["as_of"], tz="UTC") for d in decs]), side="right") - 1
    keep = rows >= 0
    decs, rows = [d for d, k in zip(decs, keep, strict=True) if k], rows[keep]
    if not decs or not usable.any():
        return None
    i0, end = int(rows[0]), int(np.flatnonzero(usable)[-1])
    if end <= i0:
        return {"days": days[:0], "net": np.zeros(0), "bench": np.zeros(0), "rf": np.zeros(0), "held": np.zeros(0),
                "costs": np.zeros(0), "dpy": subject.dpy, "data_hash": ""}
    window = np.zeros(len(days), dtype=bool)
    window[i0:end + 1] = True

    if isinstance(subject, PanelSubject):
        panel = subject.panel
        target = np.full((len(days), panel.n), np.nan)
        for d, r in zip(decs, rows, strict=True):
            target[r] = [d["targets"].get(s, 0.0) for s in panel.symbols]
        w = pd.DataFrame(target).ffill().fillna(0.0).to_numpy()
        part = panel.slice(window)
        res = run_portfolio(part, w[window])
        out = {"net": res.net, "bench": res.benchmark, "rf": part.rf, "held": np.abs(res.held).sum(axis=1),
               "costs": res.costs, "data_hash": panel_hash(part)}
    else:
        sym = subject.meta["symbol"]
        target = pd.Series(np.nan, index=subject.bars.index)
        for d, r in zip(decs, rows, strict=True):
            target.iloc[r] = d["targets"].get(sym, 0.0)
        target = target.ffill().fillna(0.0)
        bars = subject.bars[window].reset_index(drop=True)
        rf = None if subject.rf_bars is None else subject.rf_bars[window].reset_index(drop=True)
        res = run_bars(bars, target[window].reset_index(drop=True), subject.model, rf, with_trades=False)
        out = {"net": res.returns.to_numpy(), "bench": res.benchmark.to_numpy(),
               "rf": np.zeros(len(bars)) if rf is None else rf.to_numpy(), "held": np.abs(res.position.to_numpy()),
               "costs": res.costs.to_numpy(), "data_hash": store.data_hash(bars)}
    # Row 0 is the first decision's own date: its return happened before the decision.
    out = {k: (v[1:] if isinstance(v, np.ndarray) else v) for k, v in out.items()}
    return {**out, "days": days[window][1:], "dpy": subject.dpy}


def build_report(item: dict[str, Any], fwd: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """Forward statistics and chart series for one entry.

    Args:
        item: Entry row (decoded).
        fwd: Output of ``replay``, or None.
        now: Time of the scoring.

    Returns:
        ``entry``, ``scored_at``, ``days_in_book``, ``through``, ``n_days``, ``decisions`` (count, late count, last
        decision, recent rows), ``stats`` (strategy and benchmark summaries), ``band`` (forward edge Sharpe against
        the card's research band, and how many days the evidence needs), ``alarm`` (the CUSUM decay alarm) and
        ``series`` for the charts.
    """
    ref = item["reference"]
    card = ref.get("card") or {}
    dpy = int(ref.get("dpy") or 252)
    decs = book.decisions(item["entry_id"])
    added = datetime.fromisoformat(item["added_at"])
    n = 0 if fwd is None else len(fwd["net"])
    rep: dict[str, Any] = {
        "entry": {k: item[k] for k in ("entry_id", "card_id", "kind", "question", "family", "strategy", "scope",
                                       "params", "verdict", "added_at", "status", "note", "retired_at")},
        "reference": {k: ref.get(k) for k in ("edge_sharpe", "beta", "research", "n_days", "trial_id")},
        "scored_at": naive_utc(now).isoformat(timespec="seconds"),
        "days_in_book": (naive_utc(now) - added).days,
        "through": str(fwd["days"][-1].date()) if n else None,
        "n_days": n,
        "decisions": {
            "n": len(decs),
            "late": sum(d["lead_hours"] < 0 for d in decs),
            "last": decs[-1] if decs else None,
            "rows": decs[-MAX_DECISION_ROWS:][::-1],
        },
    }

    sr0, skew, kurt, rho = card.get("sr"), card.get("skew") or 0.0, card.get("kurt") or 3.0, card.get("rho") or 0.0
    if sr0 is None or not math.isfinite(sr0):
        sr0 = ref["edge_sharpe"] / math.sqrt(dpy) if ref.get("edge_sharpe") is not None else float("nan")
        skew, kurt, rho = ref.get("edge_skew", 0.0), ref.get("edge_kurt", 3.0), ref.get("edge_rho", 0.0)
    horizon = monitor.evidence_days(sr0, skew, kurt, rho)
    band: dict[str, Any] = {"research": _ann(sr0, dpy), "forward": None, "floor": None, "ceiling": None,
                            "status": None, "evidence_days": horizon if math.isfinite(horizon) else None,
                            "evidence_years": horizon / dpy if math.isfinite(horizon) else None}
    if not n:
        rep.update(stats=None, band=band, alarm=None, series=None)
        return rep

    net, bench, rf = fwd["net"], fwd["bench"], fwd["rf"]
    e = monitor.forward_edge(net, bench, rf, ref["beta"])
    sr_f = stats.sharpe(e)
    floor, ceiling = monitor.sharpe_band(sr0, n, skew, kurt, rho)
    if math.isfinite(sr_f) and math.isfinite(floor):
        band["status"] = "below" if sr_f < floor else "above" if sr_f > ceiling else "inside"
    band.update(forward=_ann(sr_f, dpy), floor=_ann(floor, dpy), ceiling=_ann(ceiling, dpy))

    alarm = monitor.decay_alarm(e, ref)
    first = alarm.pop("first_alarm")
    alarm["first_alarm"] = str(fwd["days"][first].date()) if first is not None else None
    path = alarm.pop("series")

    s_net, s_bench = pd.Series(net), pd.Series(bench)
    strat = metrics.summary(s_net, pd.Series(rf), dpy, benchmark=s_bench) if n >= 2 else {}
    strat.update(total_return=float(np.prod(1 + net) - 1), total_edge=float(e.sum()),
                 exposure=float(np.mean(fwd["held"])), costs=float(fwd["costs"].sum()))
    eq_band = monitor.equity_band(n, ref["log_mean"], ref["log_sd"], ref.get("log_rho", 0.0))
    ed_band = monitor.edge_band(n, ref["edge_mean"], ref["edge_sd"], ref.get("edge_rho", 0.0))
    eq = metrics.equity_curve(net)
    rep.update(
        stats={"strategy": strat,
               "benchmark": {"total_return": float(np.prod(1 + bench) - 1),
                             **(metrics.summary(s_bench, pd.Series(rf), dpy) if n >= 2 else {})}},
        band=band,
        alarm=alarm,
        series={
            "time": [int(t.timestamp()) for t in fwd["days"]],
            "equity": eq.tolist(),
            "benchmark": metrics.equity_curve(bench).tolist(),
            "expected": eq_band["mid"].tolist(), "expected_lo": eq_band["lo"].tolist(),
            "expected_hi": eq_band["hi"].tolist(),
            "edge": np.cumsum(e).tolist(),
            "edge_expected": ed_band["mid"].tolist(), "edge_lo": ed_band["lo"].tolist(),
            "edge_hi": ed_band["hi"].tolist(),
            "cusum": [float(x) for x in path],
            "exposure": fwd["held"].tolist(),
        },
    )
    return rep


def report_path(entry_id: str):
    """Where an entry's latest forward report is saved.

    Args:
        entry_id: Entry id.

    Returns:
        ``reports/paper/<entry_id>.json``.
    """
    return config.reports_dir() / "paper" / f"{entry_id}.json"


def load_report(entry_id: str) -> dict[str, Any] | None:
    """An entry's latest saved forward report.

    Args:
        entry_id: Entry id.

    Returns:
        The report, or None before the first scoring.
    """
    path = report_path(entry_id)
    return json.loads(path.read_text()) if path.exists() else None


def score_entry(item: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Score one entry, save its report, and record a trial when new bars were scored.

    Args:
        item: Entry row (decoded).
        now: Time of the scoring (tz-aware); None means now.

    Returns:
        The report, with ``trial_id`` set when a trial was recorded this time.
    """
    now = now or now_utc()
    fwd = replay(item, now)
    rep = db.json_safe(build_report(item, fwd, now))
    rep["trial_id"] = None
    last = db.scalar("SELECT max(through) FROM paper_scores WHERE entry_id = ?", [item["entry_id"]])
    if rep["through"] and (last is None or str(last) < rep["through"]):
        s = rep["stats"]["strategy"]
        rep["trial_id"] = ledger.record_trial(
            question=book.paper_question(item["question"]), family=item["family"], strategy=item["strategy"],
            params=item["params"], symbol=item["scope"], start=str(fwd["days"][0].date()), end=rep["through"],
            source=book.PAPER_SOURCE, metrics={**s, "forward_edge_sharpe": rep["band"]["forward"]},
            data_hash=fwd["data_hash"],
        )
        summary = {"forward_edge_sharpe": rep["band"]["forward"], "band": rep["band"]["status"],
                   "total_return": s.get("total_return"), "alarm": (rep["alarm"] or {}).get("alarm")}
        with db.connect() as con:
            con.execute("INSERT INTO paper_scores VALUES (?, ?, ?, ?, ?, ?)",
                        [item["entry_id"], naive_utc(now), rep["through"], rep["n_days"], db.dumps(summary),
                         rep["trial_id"]])
    path = report_path(item["entry_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep))
    return rep


def score(now: datetime | None = None, actor: str = "user", entry_ids: list[str] | None = None) -> list[dict]:
    """Score every entry of the book (active and retired).

    Args:
        now: Time of the scoring (tz-aware); None means now.
        actor: Who ran it, for the audit log.
        entry_ids: Only these entries; None means all.

    Returns:
        One short result per entry: ``entry_id, question, status`` (``scored`` or ``error``), ``through``,
        ``n_days``, ``forward_edge_sharpe``, ``band``, ``alarm``, ``trial_id`` and ``message``.
    """
    now = now or now_utc()
    out = []
    for item in book.entries():
        if entry_ids is not None and item["entry_id"] not in entry_ids:
            continue
        try:
            rep = score_entry(item, now)
            alarm = rep["alarm"] or {}
            out.append({"entry_id": item["entry_id"], "question": item["question"], "status": "scored",
                        "through": rep["through"], "n_days": rep["n_days"],
                        "forward_edge_sharpe": rep["band"]["forward"], "band": rep["band"]["status"],
                        "alarm": alarm.get("alarm"), "trial_id": rep["trial_id"],
                        "message": f"{rep['n_days']} forward days" + (f" through {rep['through']}" if rep["through"]
                                                                       else "")})
        except (FileNotFoundError, KeyError, ValueError) as err:
            out.append({"entry_id": item["entry_id"], "question": item["question"], "status": "error",
                        "message": str(err)})
    alarms = [r["entry_id"] for r in out if r.get("alarm")]
    audit.log("paper.score", target="book", actor=actor,
              details={"scored": sum(r["status"] == "scored" for r in out), "alarms": alarms,
                       "trials": sum(bool(r.get("trial_id")) for r in out)})
    return out
