"""Replication of approved specs: the lab's run against the paper's claim, then after publication, then the gauntlet.

Design (decision 0010):

- **Replication** runs the spec's template on its target over the paper's claimed sample, clipped to the data the
  lab has and to the research period (the lockbox is never touched). The run's Sharpe ratio is compared with the
  claimed one. The tolerance is the larger of 0.3 and two standard errors of a Sharpe estimate over that many years
  (``√((1 + SR²/2) / years)``): the lab trades ETFs or crypto where papers often used futures, stocks or other
  periods, so close is all that can be asked. A paper that claims no Sharpe ratio gets the run without a verdict.
- Both runs load two years of **history** before their window (``HISTORY_DAYS``), so a 12-month rule is already
  positioned when the window opens instead of sitting flat through its warm-up; only the window is measured. A
  window opens no earlier than two years after the target's data begins, so no measured day is a warm-up day.
- **Post-publication** runs the same rule from the paper's publication date to the end of the research period.
  McLean & Pontiff (2016) found published anomalies lose about half their returns after publication; this is the
  lab's own check. Papers published after the lockbox date have no post-publication research data at all: their
  forward evidence has to come from the paper book.
- Every run is a ledger trial (source ``inbox``) on the template's research question, so a paper the lab tries
  raises N for every later candidate on that instrument or universe, as any other trial does.
- **The gauntlet** is a separate step the owner triggers; it judges the spec like any other candidate.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from typing import Any

from .. import audit, db, lockbox
from ..backtest import runner as bar_runner
from ..data import store
from ..portfolio import runner as portfolio_runner
from .spec import catalog, get

SOURCE = "inbox"
HISTORY_DAYS = 760  # calendar days loaded before a window: the longest lookback (378 bars) plus volatility windows


def tolerance(sharpe: float, years: float) -> float:
    """How far a replicated Sharpe ratio may sit from the claimed one and still count as replicated.

    Args:
        sharpe: Claimed annual Sharpe ratio.
        years: Length of the replication window.

    Returns:
        The larger of 0.3 and two standard errors of an annual Sharpe estimate over ``years``.
    """
    if years <= 0:
        return float("inf")
    return max(0.3, 2.0 * math.sqrt((1.0 + sharpe * sharpe / 2.0) / years))


def _run(spec: dict[str, Any], start: str | None, end: str | None) -> dict[str, Any]:
    """Run a spec's template once over a window, with history before it, and record it as a ledger trial.

    Args:
        spec: The spec (template, kind, target, params).
        start: First date or None; the window opens no earlier than ``HISTORY_DAYS`` after the data begins.
        end: Last date or None (clamped to the research period).

    Returns:
        ``start, end, years, sharpe, edge_sharpe, cagr, max_dd, trial_id, question, history_from``.

    Raises:
        ValueError: No data in the window.
        FileNotFoundError: The target's data isn't ingested.
    """
    if spec["kind"] == "portfolio":
        first = portfolio_runner.load_panel(spec["target"]).ts[0].date()
    else:
        first = store.read_series(catalog()["instruments"][spec["target"]], spec["target"], "1d")["ts"].iloc[0].date()
    start = str(max(date.fromisoformat(start[:10]) if start else first, first + timedelta(days=HISTORY_DAYS)))
    history = str(date.fromisoformat(start) - timedelta(days=HISTORY_DAYS))
    if spec["kind"] == "portfolio":
        _, _, summary = portfolio_runner.backtest(spec["target"], spec["template"], spec["params"], start, end,
                                                  record_as=SOURCE, history_from=history)
    else:
        source = catalog()["instruments"][spec["target"]]
        _, summary = bar_runner.backtest(source, spec["target"], "1d", spec["template"], spec["params"], "auto",
                                         start, end, record_as=SOURCE, history_from=history)
    m = summary["metrics"]
    years = (date.fromisoformat(summary["end"]) - date.fromisoformat(summary["start"])).days / 365.25
    return {"start": summary["start"], "end": summary["end"], "years": round(years, 2), "sharpe": m.get("sharpe"),
            "edge_sharpe": m.get("edge_sharpe"), "cagr": m.get("cagr"), "max_dd": m.get("max_dd"),
            "trial_id": summary["trial_id"], "question": summary["question"],
            "history_from": summary.get("history_from")}


def replicate(spec_id: str, actor: str = "user") -> dict[str, Any]:
    """Replicate an approved spec over its claimed sample, then test it after publication.

    Args:
        spec_id: An approved (or already replicated) spec.
        actor: Who ran it.

    Returns:
        The spec with ``results``: ``in_sample`` (the run, or ``error``), ``claimed``, ``verdict`` (``replicated``,
        ``not_replicated`` or ``no_claim``), ``tolerance``, and ``post_publication`` (the run, or the reason there
        is none).

    Raises:
        KeyError: Unknown spec.
        ValueError: The spec isn't approved.
    """
    s = get(spec_id)
    if s["status"] not in ("approved", "replicated", "not_replicated"):
        raise ValueError(f"only approved specs are replicated; this one is {s['status']}")
    spec = s["spec"]
    research_end = lockbox.research_end()
    results: dict[str, Any] = {"claimed": {k: spec.get(k) for k in ("claimed_sharpe", "claimed_cagr", "sample_start",
                                                                    "sample_end", "costs_included")}}
    try:
        results["in_sample"] = _run(spec, spec.get("sample_start"), spec.get("sample_end") or research_end)
    except (ValueError, FileNotFoundError, KeyError) as err:
        results["in_sample"] = {"error": str(err)}
    run = results["in_sample"]
    claimed = spec.get("claimed_sharpe")
    if "error" in run or claimed is None or run.get("sharpe") is None:
        verdict, tol = "no_claim" if claimed is None else "not_replicated", None
    else:
        tol = tolerance(float(claimed), run["years"])
        verdict = "replicated" if abs(run["sharpe"] - float(claimed)) <= tol else "not_replicated"
    results.update(verdict=verdict, tolerance=tol)
    published = spec.get("published")
    if not published or published > research_end:
        results["post_publication"] = {"reason": f"published {published or 'on an unknown date'}, after the research "
                                                 f"period ends ({research_end}); forward evidence must come from the "
                                                 "paper book"}
    else:
        try:
            results["post_publication"] = _run(spec, published, research_end)
        except (ValueError, FileNotFoundError, KeyError) as err:
            results["post_publication"] = {"reason": str(err)}
    status = "replicated" if verdict == "replicated" else ("not_replicated" if verdict == "not_replicated"
                                                          else "approved")
    with db.connect() as con:
        con.execute("UPDATE strategy_specs SET results = ?, status = ? WHERE spec_id = ?",
                    [db.dumps(db.json_safe(results)), status, spec_id])
    audit.log("inbox.replicate", target=spec_id, actor=actor,
              details={"verdict": verdict, "sharpe": run.get("sharpe"), "claimed": claimed})
    return get(spec_id)


def judge(spec_id: str, actor: str = "user") -> dict[str, Any]:
    """Send a replicated (or approved) spec through the gauntlet.

    Args:
        spec_id: Spec id.
        actor: Who sent it.

    Returns:
        The spec, with ``card_id`` and status ``judged``.

    Raises:
        KeyError: Unknown spec.
        ValueError: The spec isn't approved, or the gauntlet refused (too little research data).
    """
    from ..gauntlet import pipeline

    s = get(spec_id)
    if s["status"] not in ("approved", "replicated", "not_replicated", "judged"):
        raise ValueError(f"only approved specs go to the gauntlet; this one is {s['status']}")
    spec = s["spec"]
    if spec["kind"] == "portfolio":
        card = pipeline.run_portfolio_gauntlet(spec["target"], spec["template"], spec["params"], actor=actor)
    else:
        card = pipeline.run_gauntlet(catalog()["instruments"][spec["target"]], spec["target"], "1d",
                                     spec["template"], spec["params"], "auto", actor=actor)
    with db.connect() as con:
        con.execute("UPDATE strategy_specs SET card_id = ?, status = 'judged' WHERE spec_id = ?",
                    [card["card_id"], spec_id])
    audit.log("inbox.judge", target=spec_id, actor=actor,
              details={"card_id": card["card_id"], "verdict": card["verdict"], "failed": card["failed_stage"]})
    return get(spec_id)


def compare() -> list[dict[str, Any]]:
    """Claimed, replicated and post-publication Sharpe ratios of every spec that has been run, for the chart.

    Returns:
        One row per spec: ``spec_id, title, template, target, claimed, replicated, post_publication, verdict``.
    """
    out = []
    for r in db.rows("""SELECT s.spec_id, s.spec, s.results, p.title FROM strategy_specs s
                        LEFT JOIN papers p ON p.arxiv_id = s.arxiv_id WHERE s.results IS NOT NULL"""):
        spec, res = json.loads(r["spec"]), json.loads(r["results"])
        out.append({"spec_id": r["spec_id"], "title": r["title"], "template": spec["template"],
                    "target": spec["target"], "claimed": spec.get("claimed_sharpe"),
                    "replicated": (res.get("in_sample") or {}).get("sharpe"),
                    "post_publication": (res.get("post_publication") or {}).get("sharpe"),
                    "verdict": res.get("verdict")})
    return out


__all__ = ["compare", "judge", "replicate", "tolerance"]
