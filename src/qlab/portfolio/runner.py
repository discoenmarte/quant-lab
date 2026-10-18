"""Run a portfolio strategy on a universe, record the trial, and build the report.

Mirrors ``backtest.runner`` for panels: runs stop before the lockbox unless asked, every run is a ledger trial,
and the research question is ``family:universe:1d`` so N is counted per universe across families.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from .. import audit, config, db, ledger, lockbox
from ..backtest import metrics
from ..data import store
from .engine import PortfolioResult, run_portfolio
from .panel import UNIVERSES, Panel, load_panel
from .strategies import REGISTRY

MAX_CHART_POINTS = 4000


def question_of(family: str, universe: str) -> str:
    """Research question key for a portfolio family on a universe.

    Args:
        family: Strategy family.
        universe: Universe name.

    Returns:
        ``"family:universe:1d"``.
    """
    return f"{family}:{universe}:1d"


def panel_hash(panel: Panel) -> str:
    """Fingerprint of a panel's prices.

    Args:
        panel: Price panel.

    Returns:
        A 16-character hex hash.
    """
    frame = pd.DataFrame(np.hstack([panel.open, panel.close]), index=panel.ts)
    return store.data_hash(frame.reset_index())


def clip_to(panel: Panel, start: str | None, end: str | None) -> Panel:
    """A panel restricted to a date range.

    Args:
        panel: Price panel.
        start: First date (inclusive), or None.
        end: Last date (inclusive), or None.

    Returns:
        The clipped panel.
    """
    mask = np.ones(len(panel.ts), dtype=bool)
    if start:
        mask &= panel.ts >= pd.Timestamp(start, tz="UTC")
    if end:
        mask &= panel.ts < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    return panel.slice(mask)


def backtest(
    universe: str,
    strategy: str,
    params: dict[str, float] | None = None,
    start: str | None = None,
    end: str | None = None,
    record_as: str | None = "studio",
    include_lockbox: bool = False,
) -> tuple[Panel, PortfolioResult, dict[str, Any]]:
    """Backtest a portfolio strategy and, unless told not to, record it in the ledger.

    Args:
        universe: Key of ``panel.UNIVERSES``.
        strategy: Key of ``strategies.REGISTRY``.
        params: Parameter overrides.
        start: First date, ``YYYY-MM-DD``.
        end: Last date, ``YYYY-MM-DD``; clamped to the day before the lockbox unless ``include_lockbox``.
        record_as: Ledger source tag; None skips recording.
        include_lockbox: Let the run reach into the sealed period, spending this question's lockbox.

    Returns:
        ``(panel, result, summary)``; the summary holds metrics against equal weight, the trial id and N.

    Raises:
        KeyError: Unknown universe or strategy.
        FileNotFoundError: An instrument isn't ingested.
        ValueError: No dates in the requested period.
    """
    strat = REGISTRY[strategy]
    full_params = {**strat.defaults(), **(params or {})}
    panel = clip_to(load_panel(universe), start, end if include_lockbox else lockbox.clamp_end(end))
    if len(panel.ts) < 2:
        raise ValueError("no dates in the requested period before the lockbox")
    res = run_portfolio(panel, strat.weights(panel, full_params))
    stats = metrics.summary(pd.Series(res.net), pd.Series(panel.rf), panel.dpy,
                            benchmark=pd.Series(res.benchmark))
    stats["gross_exposure"] = float(np.abs(res.held).sum(axis=1).mean())
    stats["turnover"] = float(res.turnover.sum() / len(panel.ts) * panel.dpy)
    question = question_of(strat.family, universe)
    summary: dict[str, Any] = {
        "question": question,
        "strategy": strategy,
        "family": strat.family,
        "params": full_params,
        "universe": universe,
        "symbols": panel.symbols,
        "start": str(panel.ts[0].date()),
        "end": str(panel.ts[-1].date()),
        "metrics": stats,
        "trial_id": None,
    }
    if record_as:
        summary["trial_id"] = ledger.record_trial(
            question=question, family=strat.family, strategy=strategy, params=full_params, symbol=universe,
            start=summary["start"], end=summary["end"], source=record_as, metrics=stats, data_hash=panel_hash(panel),
        )
    summary["n_trials"] = ledger.count_trials(question)
    sealed = lockbox.sealed_from()
    summary["lockbox"] = {"sealed_from": str(sealed.date()), "included": bool(panel.ts[-1] >= sealed),
                          "spent": lockbox.spent(question)}
    return panel, res, summary


def report(panel: Panel, res: PortfolioResult, summary: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON report the Portfolio Lab renders, and save it under ``reports/portfolios``.

    Args:
        panel: The panel the strategy ran on.
        res: Engine output.
        summary: Output of ``backtest``.

    Returns:
        ``meta``, ``stats`` (strategy and equal weight), ``series`` (time, equity, benchmark equity, drawdown,
        gross exposure), ``weights`` (per instrument over time, downsampled), ``latest`` (weights now),
        ``contribution`` (yearly return contribution per instrument) and ``monthly`` returns.
    """
    step = max(1, int(np.ceil(len(panel.ts) / MAX_CHART_POINTS)))
    ts = panel.ts
    eq = metrics.equity_curve(res.net)
    beq = metrics.equity_curve(res.benchmark)
    prev = np.vstack([panel.close[:1], panel.close[:-1]])
    asset_r = panel.close / prev - 1.0
    contrib = (res.held * asset_r).sum(axis=0) / (len(ts) / panel.dpy)
    rep = {
        "meta": {k: summary[k] for k in ("question", "strategy", "family", "params", "universe", "symbols", "start",
                                          "end", "trial_id", "n_trials", "lockbox")},
        "stats": {
            "strategy": summary["metrics"],
            "benchmark": metrics.summary(pd.Series(res.benchmark), pd.Series(panel.rf), panel.dpy),
        },
        "series": {
            "time": [int(t.timestamp()) for t in ts[::step]],
            "equity": [float(x) for x in eq[::step]],
            "benchmark": [float(x) for x in beq[::step]],
            "drawdown": [float(x) for x in metrics.drawdown_series(res.net)[::step]],
            "gross": [float(x) for x in np.abs(res.held).sum(axis=1)[::step]],
        },
        "weights": {sym: [round(float(x), 4) for x in res.held[::step, i]] for i, sym in enumerate(panel.symbols)},
        "latest": {sym: float(res.held[-1, i]) for i, sym in enumerate(panel.symbols)},
        "contribution": {sym: float(contrib[i]) for i, sym in enumerate(panel.symbols)},
        "monthly": metrics.monthly_returns(pd.Series(res.net, index=ts.tz_localize(None))),
        "costs": {"per_year": float(res.costs.sum() / (len(ts) / panel.dpy)),
                  "cash_per_year": float(res.cash.sum() / (len(ts) / panel.dpy)),
                  "turnover_per_year": summary["metrics"]["turnover"]},
    }
    if summary["trial_id"]:
        out = config.reports_dir() / "portfolios" / f"{summary['trial_id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(db.json_safe(rep)))
        audit.log("portfolio.run", target=summary["trial_id"],
                  details={"question": summary["question"], "edge_sharpe": summary["metrics"].get("edge_sharpe")})
    return rep


def load_report(trial_id: str) -> dict[str, Any]:
    """Load a saved portfolio report.

    Args:
        trial_id: Ledger trial id.

    Returns:
        The report.

    Raises:
        FileNotFoundError: No report for this trial.
    """
    path = config.reports_dir() / "portfolios" / f"{trial_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no portfolio report for trial {trial_id}")
    return json.loads(path.read_text())


def universes() -> list[dict[str, Any]]:
    """Every named universe with its instruments and, when ingested, its first date.

    Returns:
        Rows ``name, description, members, first, n_days`` (``first`` is None when something isn't ingested).
    """
    out = []
    for name, u in UNIVERSES.items():
        try:
            p = load_panel(name)
            first, n_days = str(p.ts[0].date()), len(p.ts)
        except (FileNotFoundError, ValueError):
            first, n_days = None, 0
        out.append({"name": name, "description": u["description"], "members": [s for _, s in u["members"]],
                    "first": first, "n_days": n_days})
    return out
