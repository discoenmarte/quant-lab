"""Run a registered strategy on stored data, record the trial, and build the report.

This is the single entry point the Backtest Studio, the gauntlet and mining use, so every backtest lands in
the ledger the same way.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from .. import audit, config, ledger, lockbox
from ..data import store
from ..data.ingest import SOURCES
from . import metrics
from .engine import COST_PRESETS, BacktestResult, CostModel, run
from .strategies import REGISTRY

MAX_CHART_POINTS = 4000


def periods_per_year(source: str, interval: str) -> int:
    """Bars per year for annualising.

    Args:
        source: Source name; crypto trades every day, ETFs about 252 days.
        interval: ``"1d"`` or ``"1h"``.

    Returns:
        252 or 365 for daily bars; hours per year for hourly bars.
    """
    days = 365 if SOURCES.get(source) and SOURCES[source].asset_class == "crypto" else 252
    if interval == "1d":
        return days
    hours = 24 if days == 365 else 6.5
    return int(days * hours) if interval == "1h" else days


def load_bars(
    source: str, symbol: str, interval: str, start: str | None = None, end: str | None = None
) -> pd.DataFrame:
    """Stored bars for a period, with a fresh positional index.

    Args:
        source: Source name.
        symbol: Instrument.
        interval: Bar interval.
        start: First date (inclusive), ``YYYY-MM-DD``. None means the beginning.
        end: Last date (inclusive). None means the latest bar.

    Returns:
        Normalised bars.
    """
    bars = store.read_series(source, symbol, interval)
    if start:
        bars = bars[bars["ts"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        bars = bars[bars["ts"] < pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)]  # through 23:59 that day
    return bars.reset_index(drop=True)


def risk_free(source: str, bars: pd.DataFrame, interval: str) -> pd.Series | None:
    """Risk-free return per bar for idle cash.

    ETFs earn the 1-month T-bill rate from the Ken French file. Crypto cash earns nothing (stablecoin yield
    is a separate, riskier bet).

    Args:
        source: Source of the bars.
        bars: Normalised bars.
        interval: Bar interval; only daily bars get a rate.

    Returns:
        A series aligned with ``bars``, or None when no rate applies or the factors aren't ingested.
    """
    if interval != "1d" or SOURCES.get(source) is None or SOURCES[source].asset_class != "equity":
        return None
    try:
        ff = store.read_series("french", "FF3", "1d")
    except FileNotFoundError:
        return None
    rf = ff.set_index("ts")["rf"]
    aligned = rf.reindex(bars["ts"]).ffill().fillna(0.0)
    return pd.Series(aligned.to_numpy(), index=bars.index)


def resolve_costs(costs: str | dict[str, float] | CostModel, source: str) -> CostModel:
    """Turn a preset name, a dict or a model into a ``CostModel``.

    Args:
        costs: ``"auto"`` (by asset class), a preset name, a dict of ``CostModel`` fields, or a model.
        source: Source name, for ``"auto"``.

    Returns:
        The cost model.

    Raises:
        KeyError: Unknown preset name.
    """
    if isinstance(costs, CostModel):
        return costs
    if isinstance(costs, dict):
        return CostModel(**costs)
    if costs == "auto":
        crypto = SOURCES.get(source) is not None and SOURCES[source].asset_class == "crypto"
        return COST_PRESETS["crypto" if crypto else "etf"]
    return COST_PRESETS[costs]


def backtest(
    source: str,
    symbol: str,
    interval: str,
    strategy: str,
    params: dict[str, float] | None = None,
    costs: str | dict[str, float] | CostModel = "auto",
    start: str | None = None,
    end: str | None = None,
    record_as: str | None = "studio",
    include_lockbox: bool = False,
) -> tuple[BacktestResult, dict[str, Any]]:
    """Backtest a registered strategy and, unless told not to, record it in the ledger.

    Runs stop the day before the lockbox unless ``include_lockbox`` is set. Including it is allowed, but the
    trial then covers sealed dates, which spends the lockbox of this research question (see ``qlab.lockbox``).

    Args:
        source: Source name.
        symbol: Instrument.
        interval: Bar interval.
        strategy: Registry key.
        params: Parameter overrides.
        costs: Cost preset, dict or model (see ``resolve_costs``).
        start: First date, ``YYYY-MM-DD``.
        end: Last date, ``YYYY-MM-DD``.
        record_as: Ledger source tag (``studio``, ``gauntlet``, ``mining``...). None skips recording, which
            only internal re-runs of an already-recorded trial should do.
        include_lockbox: Let the run reach into the sealed period, spending this question's lockbox.

    Returns:
        ``(result, summary)`` where summary holds the metrics (including edge Sharpe), the trial id, N for the
        question, and ``lockbox`` (sealed date, whether this run included it, whether the question's lockbox
        is spent).

    Raises:
        KeyError: Unknown strategy.
        FileNotFoundError: The series isn't ingested.
    """
    strat = REGISTRY[strategy]
    full_params = {**strat.defaults(), **(params or {})}
    bars = load_bars(source, symbol, interval, start, end if include_lockbox else lockbox.clamp_end(end))
    if bars.empty:
        raise ValueError("no bars in the requested period before the lockbox")
    model = resolve_costs(costs, source)
    rf = risk_free(source, bars, interval)
    result = run(bars, strat.positions(bars, full_params), model, rf)
    ppy = periods_per_year(source, interval)
    stats = metrics.summary(result.returns, rf, ppy, result.position, benchmark=result.benchmark)
    question = f"{strat.family}:{symbol}:{interval}"
    summary: dict[str, Any] = {
        "question": question,
        "strategy": strategy,
        "params": full_params,
        "source": source,
        "symbol": symbol,
        "interval": interval,
        "costs": model.__dict__,
        "start": str(bars["ts"].iloc[0].date()) if len(bars) else None,
        "end": str(bars["ts"].iloc[-1].date()) if len(bars) else None,
        "metrics": stats,
        "trial_id": None,
    }
    if record_as:
        summary["trial_id"] = ledger.record_trial(
            question=question, family=strat.family, strategy=strategy, params=full_params, symbol=symbol,
            start=summary["start"], end=summary["end"], source=record_as, metrics=stats,
            data_hash=store.data_hash(bars),
        )
    summary["n_trials"] = ledger.count_trials(question)
    sealed = lockbox.sealed_from()
    summary["lockbox"] = {"sealed_from": str(sealed.date()), "included": bool(bars["ts"].iloc[-1] >= sealed),
                          "spent": lockbox.spent(question)}
    return result, summary


def _downsample(n: int) -> slice:
    """Slice step so at most ``MAX_CHART_POINTS`` points are sent to a chart.

    Args:
        n: Number of points.

    Returns:
        A slice with a step of 1 or more.
    """
    return slice(None, None, max(1, int(np.ceil(n / MAX_CHART_POINTS))))


def report(result: BacktestResult, summary: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON report the Backtest Studio renders, and save it under ``reports/backtests``.

    Args:
        result: Engine output.
        summary: Output of ``backtest``.

    Returns:
        ``meta``, ``stats`` (strategy and benchmark), ``costs`` breakdown, ``series`` (time, equity,
        benchmark equity, drawdown, position), ``monthly`` returns, ``trades`` and chart ``markers``.
    """
    ppy = periods_per_year(summary["source"], summary["interval"])
    ts = pd.DatetimeIndex(result.ts)
    eq = metrics.equity_curve(result.returns)
    beq = metrics.equity_curve(result.benchmark)
    dd = metrics.drawdown_series(result.returns)
    step = _downsample(len(ts))
    years = max(len(ts) / ppy, 1e-9)
    per_unit = CostModel(**summary["costs"]).per_unit
    turnover = float(np.abs(np.diff(result.position.to_numpy(), prepend=0.0)).sum())
    parts = summary["costs"]
    total_bps = parts["commission_bps"] + parts["spread_bps"] / 2 + parts["slippage_bps"]

    def share(bps: float) -> float:
        return float(result.costs.sum() * bps / total_bps / years) if total_bps else 0.0

    trades = result.trades
    closed = trades.dropna(subset=["exit_ts"])
    rep = {
        "meta": {k: summary[k] for k in ("strategy", "params", "source", "symbol", "interval", "start", "end",
                                          "question", "trial_id", "n_trials", "costs", "lockbox")},
        "stats": {
            "strategy": summary["metrics"],
            "benchmark": metrics.summary(result.benchmark, None, ppy),
            "n_trades": int(len(trades)),
            "win_rate": float((closed["ret"] > 0).mean()) if len(closed) else None,
            "avg_win": float(closed.loc[closed["ret"] > 0, "ret"].mean()) if (closed["ret"] > 0).any() else None,
            "avg_loss": float(closed.loc[closed["ret"] <= 0, "ret"].mean()) if (closed["ret"] <= 0).any() else None,
        },
        "costs": {
            "per_year": {
                "commission": share(parts["commission_bps"]),
                "spread": share(parts["spread_bps"] / 2),
                "slippage": share(parts["slippage_bps"]),
            },
            "cash_per_year": float(result.cash.sum() / years),
            "gross_cagr": metrics.cagr(result.gross, ppy),
            "net_cagr": metrics.cagr(result.returns, ppy),
            "turnover_per_year": turnover / years,
            "cost_per_unit": per_unit,
        },
        "series": {
            "time": [int(t.timestamp()) for t in ts[step]],
            "equity": [float(x) for x in eq[step]],
            "benchmark": [float(x) for x in beq[step]],
            "drawdown": [float(x) for x in dd[step]],
            "position": [float(x) for x in result.position.to_numpy()[step]],
        },
        "monthly": metrics.monthly_returns(pd.Series(result.returns.to_numpy(), index=ts.tz_localize(None))),
        "trades": [
            {
                "entry": t.entry_ts.isoformat() if t.entry_ts is not None else None,
                "exit": t.exit_ts.isoformat() if t.exit_ts is not None and not pd.isna(t.exit_ts) else None,
                "entry_price": t.entry_price,
                "exit_price": None if t.exit_price is None or pd.isna(t.exit_price) else float(t.exit_price),
                "side": t.side,
                "bars": int(t.bars),
                "ret": t.ret,
            }
            for t in trades.tail(500).itertuples()
        ],
    }
    if summary["trial_id"]:
        out = config.reports_dir() / "backtests" / f"{summary['trial_id']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, default=str))
        audit.log("backtest.run", target=summary["trial_id"],
                  details={"question": summary["question"], "sharpe": summary["metrics"]["sharpe"]})
    return rep


def load_report(trial_id: str) -> dict[str, Any]:
    """Load a saved backtest report.

    Args:
        trial_id: Ledger trial id.

    Returns:
        The report dict.

    Raises:
        FileNotFoundError: No report was saved for this trial.
    """
    path = config.reports_dir() / "backtests" / f"{trial_id}.json"
    return json.loads(path.read_text())
