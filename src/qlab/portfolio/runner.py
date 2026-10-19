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
MAX_WEIGHT_SERIES = 25  # instruments drawn in the weights chart; a dynamic panel's others are summed as "other"


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
    history_from: str | None = None,
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
        history_from: An earlier date to load data from, so the rule's signals are already formed on ``start``
            (a 12-month rule started cold spends its first year flat). Metrics and the trial cover only
            ``start``..``end``; None loads from ``start``.

    Returns:
        ``(panel, result, summary)``; the summary holds metrics against equal weight, the trial id and N. With
        ``history_from`` the panel and result include the history; the summary doesn't.

    Raises:
        KeyError: Unknown universe or strategy.
        FileNotFoundError: An instrument isn't ingested.
        ValueError: No dates in the requested period, or a strategy built for the other kind of universe.
    """
    strat = REGISTRY[strategy]
    full_params = {**strat.defaults(), **(params or {})}
    warm = bool(history_from and start and history_from < start)
    panel = clip_to(load_panel(universe), history_from if warm else start,
                    end if include_lockbox else lockbox.clamp_end(end))
    check_fit(strat, panel)
    i0 = int(np.searchsorted(panel.ts, pd.Timestamp(start, tz="UTC"))) if warm else 0
    if len(panel.ts) - i0 < 2:
        raise ValueError("no dates in the requested period before the lockbox")
    res = run_portfolio(panel, strat.weights(panel, full_params))
    m = slice(i0, None)
    stats = metrics.summary(pd.Series(res.net[m]), pd.Series(panel.rf[m]), panel.dpy,
                            benchmark=pd.Series(res.benchmark[m]))
    stats["gross_exposure"] = float(np.abs(res.held[m]).sum(axis=1).mean())
    stats["turnover"] = float(res.turnover[m].sum() / (len(panel.ts) - i0) * panel.dpy)
    question = question_of(strat.family, universe)
    summary: dict[str, Any] = {
        "question": question,
        "strategy": strategy,
        "family": strat.family,
        "params": full_params,
        "universe": universe,
        "symbols": panel.symbols,
        "start": str(panel.ts[i0].date()),
        "end": str(panel.ts[-1].date()),
        "history_from": str(panel.ts[0].date()) if warm else None,
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


def check_fit(strat: Any, panel: Panel) -> None:
    """Refuse a strategy on the kind of universe it wasn't built for.

    Static rules (trend, risk parity...) assume every instrument trades every day; cross-sectional rules rank
    point-in-time members and need dollar volume.

    Args:
        strat: A portfolio strategy.
        panel: The panel it would run on.

    Raises:
        ValueError: The strategy's ``kind`` doesn't match the panel.
    """
    kind = "dynamic" if panel.dynamic else "static"
    if strat.kind not in ("any", kind):
        raise ValueError(f"{strat.name} is built for {strat.kind} universes and {panel.name} is {kind}")


def report(panel: Panel, res: PortfolioResult, summary: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON report the Portfolio Lab renders, and save it under ``reports/portfolios``.

    Args:
        panel: The panel the strategy ran on.
        res: Engine output.
        summary: Output of ``backtest``.

    Returns:
        ``meta``, ``stats`` (strategy and equal weight), ``series`` (time, equity, benchmark equity, drawdown,
        gross exposure), ``weights`` (per instrument over time, downsampled; on a dynamic panel the
        ``MAX_WEIGHT_SERIES`` largest holdings plus ``other``), ``latest`` (weights now; on a dynamic panel only
        holdings), ``contribution`` (yearly return contribution per instrument ever held), ``monthly`` returns,
        ``costs`` and ``universe`` (see ``universe_stats``).
    """
    step = max(1, int(np.ceil(len(panel.ts) / MAX_CHART_POINTS)))
    ts = panel.ts
    eq = metrics.equity_curve(res.net)
    beq = metrics.equity_curve(res.benchmark)
    asset_r = np.nan_to_num(panel.returns())
    contrib = (res.held * asset_r).sum(axis=0) / (len(ts) / panel.dpy)
    held_any = np.flatnonzero(np.abs(res.held).sum(axis=0) > 0) if panel.dynamic else np.arange(panel.n)
    shown = held_any[np.argsort(-np.abs(res.held[:, held_any]).mean(axis=0))][:MAX_WEIGHT_SERIES]
    weights = {panel.symbols[i]: [round(float(x), 4) for x in res.held[::step, i]] for i in sorted(shown)}
    if panel.dynamic and len(held_any) > len(shown):
        rest = np.setdiff1d(held_any, shown)
        weights["other"] = [round(float(x), 4) for x in res.held[::step][:, rest].sum(axis=1)]
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
        "weights": weights,
        "latest": {panel.symbols[i]: float(res.held[-1, i]) for i in held_any
                   if not panel.dynamic or res.held[-1, i] != 0},
        "contribution": {panel.symbols[i]: float(contrib[i]) for i in held_any},
        "universe": universe_stats(panel),
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


def universe_stats(panel: Panel) -> dict[str, Any]:
    """How a panel's membership behaves over time, for the Portfolio Lab.

    Args:
        panel: A panel.

    Returns:
        ``kind`` (``static`` or ``dynamic``), ``top`` (members per date, None for static), ``n_instruments``,
        ``n_delisted`` (instruments whose last bar is before the panel's last date), ``members`` (count per date,
        downsampled to at most ``MAX_CHART_POINTS``) and ``time`` (matching Unix seconds).
    """
    step = max(1, int(np.ceil(len(panel.ts) / MAX_CHART_POINTS)))
    last = np.where(panel.avail.any(axis=0), len(panel.ts) - 1 - np.argmax(panel.avail[::-1], axis=0), -1)
    return {
        "kind": "dynamic" if panel.dynamic else "static",
        "top": panel.rule.top if panel.rule else None,
        "n_instruments": panel.n,
        "n_delisted": int((last < len(panel.ts) - 1).sum()),
        "members": [int(x) for x in panel.member.sum(axis=1)[::step]],
        "time": [int(t.timestamp()) for t in panel.ts[::step]],
    }


def universes() -> list[dict[str, Any]]:
    """Every named universe with its instruments and, when ingested, its first date.

    Returns:
        Rows ``name, description, kind, members, n_instruments, top, n_delisted, first, n_days``. ``members`` lists
        a static universe's instruments; a dynamic universe lists none (it has hundreds), and gives
        ``n_instruments`` (ever members) and ``n_delisted`` instead. ``first`` is None when something isn't
        ingested.
    """
    out = []
    for name, u in UNIVERSES.items():
        dynamic = u["members"] == "binance_usdt"
        row: dict[str, Any] = {"name": name, "description": u["description"],
                               "kind": "dynamic" if dynamic else "static",
                               "members": [] if dynamic else [s for _, s in u["members"]],
                               "top": u["membership"].top if dynamic else None,
                               "n_instruments": None if dynamic else len(u["members"]), "n_delisted": None}
        try:
            p = load_panel(name)
            row.update(first=str(p.ts[0].date()), n_days=len(p.ts))
            if dynamic:
                st = universe_stats(p)
                row.update(n_instruments=st["n_instruments"], n_delisted=st["n_delisted"])
        except (FileNotFoundError, ValueError):
            row.update(first=None, n_days=0)
        out.append(row)
    return out
