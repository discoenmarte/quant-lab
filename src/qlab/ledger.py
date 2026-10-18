"""The trial ledger: every backtest the lab runs, from any source, is recorded here.

The ledger keeps the statistics honest. The Deflated Sharpe Ratio needs the number of trials N and the
spread of Sharpe ratios across them for each research question. Nothing skips the ledger, including manual
runs from the Backtest Studio, mining and calibration.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import date
from typing import Any

import pandas as pd

from . import db
from .timeutil import naive_utc

COLUMNS = ["trial_id", "created_at", "question", "family", "strategy", "params", "symbol", "start_date", "end_date",
           "source", "sharpe", "cagr", "max_dd", "n_obs", "metrics", "data_hash", "edge_sharpe"]


def record_trial(
    *,
    question: str,
    family: str,
    strategy: str,
    params: dict[str, Any],
    symbol: str,
    start: date | str | None,
    end: date | str | None,
    source: str,
    metrics: dict[str, Any],
    data_hash: str = "",
) -> str:
    """Record one backtest and return its trial id.

    Args:
        question: Research question the trial belongs to, e.g. ``"trend_sma:SPY:1d"``. N is counted per question.
        family: Strategy family, e.g. ``"trend_sma"``. The lockbox is tracked per family.
        strategy: Strategy name within the family.
        params: Parameters used, JSON-serialisable.
        symbol: Instrument tested.
        start: First date of the tested period.
        end: Last date of the tested period.
        source: Where the trial came from: ``studio``, ``gauntlet``, ``lockbox``, ``mining``, ``calibration``,
            ``demo``.
        metrics: Output of ``backtest.metrics.summary``. Sharpe, CAGR, max drawdown and edge Sharpe are also
            stored in their own columns for fast queries.
        data_hash: Fingerprint of the input data, so results can be traced to a data version.

    Returns:
        A 12-character hex trial id.
    """
    return record_trials([dict(question=question, family=family, strategy=strategy, params=params, symbol=symbol,
                               start=start, end=end, source=source, metrics=metrics, data_hash=data_hash)])[0]


def record_trials(trials: list[dict[str, Any]]) -> list[str]:
    """Record many backtests in one write. The gauntlet, mining and calibration produce trials in batches.

    Args:
        trials: Dicts with the keyword arguments of ``record_trial``.

    Returns:
        The new trial ids, in input order.
    """
    if not trials:
        return []
    now = naive_utc()
    ids = [uuid.uuid4().hex[:12] for _ in trials]
    frame = pd.DataFrame(
        [
            {
                "trial_id": tid,
                "created_at": now,
                "question": t["question"],
                "family": t["family"],
                "strategy": t["strategy"],
                "params": db.dumps(t["params"]),
                "symbol": t["symbol"],
                "start_date": str(t["start"])[:10] if t.get("start") else None,
                "end_date": str(t["end"])[:10] if t.get("end") else None,
                "source": t["source"],
                "sharpe": _finite(t["metrics"].get("sharpe")),
                "cagr": _finite(t["metrics"].get("cagr")),
                "max_dd": _finite(t["metrics"].get("max_dd")),
                "n_obs": int(t["metrics"].get("n_obs") or 0),
                "metrics": db.dumps(t["metrics"]),
                "data_hash": t.get("data_hash", ""),
                "edge_sharpe": _finite(t["metrics"].get("edge_sharpe")),
            }
            for tid, t in zip(ids, trials, strict=True)
        ],
        columns=COLUMNS,
    )
    frame["start_date"] = pd.to_datetime(frame["start_date"]).dt.date
    frame["end_date"] = pd.to_datetime(frame["end_date"]).dt.date
    with db.connect() as con:
        con.register("new_trials", frame)
        con.execute(f"INSERT INTO trials ({', '.join(COLUMNS)}) SELECT {', '.join(COLUMNS)} FROM new_trials")
        con.unregister("new_trials")
    return ids


def _finite(x: Any) -> float | None:
    """Coerce a value to a finite float.

    Args:
        x: Any value.

    Returns:
        The float, or None for NaN, infinity and non-numbers (stored as SQL NULL).
    """
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def count_trials(question: str | None = None) -> int:
    """Number of recorded trials.

    Args:
        question: Count only this research question. None counts everything.

    Returns:
        Trial count N.
    """
    if question is None:
        return int(db.scalar("SELECT count(*) FROM trials") or 0)
    return int(db.scalar("SELECT count(*) FROM trials WHERE question = ?", [question]) or 0)


def sharpes(question: str) -> list[float]:
    """Annualised Sharpe ratios of every trial on a research question.

    Args:
        question: Research question key.

    Returns:
        Sharpe ratios in insertion order, skipping trials whose Sharpe was undefined.
    """
    recs = db.rows(
        "SELECT sharpe FROM trials WHERE question = ? AND sharpe IS NOT NULL ORDER BY created_at",
        [question],
    )
    return [float(r["sharpe"]) for r in recs]


def selection_set(symbol: str, interval: str) -> list[dict[str, Any]]:
    """Every distinct trial ever run on an instrument, across all strategy families, with its edge Sharpe.

    This is the set a new candidate is deflated against. It spans families because choosing between a trend
    rule and a breakout rule on the same instrument is also a choice. Re-running the same parameters on the
    same period isn't a new trial, so exact repeats collapse to one; a different period or different parameters
    count as new trials. Calibration and demo runs are excluded: they are simulated researchers, not choices
    made about this instrument. Paper-book trials are excluded too: scoring a fixed decision record on new days
    chooses nothing.

    Args:
        symbol: Instrument, e.g. ``"SPY"``.
        interval: Bar interval, e.g. ``"1d"``.

    Returns:
        One dict per distinct trial: ``strategy, params`` (canonical JSON), ``start, end`` (ISO dates) and
        ``edge_sharpe`` (annual). Trials recorded before edge Sharpe existed are skipped.
    """
    return db.rows(
        """SELECT strategy, params, CAST(start_date AS VARCHAR) AS start, CAST(end_date AS VARCHAR) AS "end",
                  avg(edge_sharpe) AS edge_sharpe
           FROM trials
           WHERE symbol = ? AND question LIKE ? AND edge_sharpe IS NOT NULL
             AND source NOT IN ('calibration', 'demo', 'paper')
           GROUP BY strategy, params, start_date, end_date""",
        [symbol, f"%:{symbol}:{interval}"],
    )


def list_trials(
    limit: int = 500,
    question: str | None = None,
    source: str | None = None,
    offset: int = 0,
    exclude_source: str | None = None,
) -> list[dict[str, Any]]:
    """Recent trials, newest first, with params and metrics decoded.

    Args:
        limit: Maximum number of trials.
        question: Restrict to one research question. None returns all.
        source: Restrict to one origin (``studio``, ``gauntlet``...). None returns all.
        offset: Number of newest trials to skip, for paging.
        exclude_source: Leave out one origin, usually ``calibration``.

    Returns:
        Trial dicts as stored, with ``params`` and ``metrics`` as dicts.
    """
    clauses, args = [], []
    if question:
        clauses.append("question = ?")
        args.append(question)
    if source:
        clauses.append("source = ?")
        args.append(source)
    if exclude_source:
        clauses.append("source <> ?")
        args.append(exclude_source)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    recs = db.rows(
        f"SELECT * FROM trials {where} ORDER BY created_at DESC, trial_id LIMIT ? OFFSET ?", [*args, limit, offset]
    )
    for r in recs:
        r["params"] = json.loads(r["params"] or "{}")
        r["metrics"] = json.loads(r["metrics"] or "{}")
    return recs


def counts_by_source() -> dict[str, int]:
    """Number of trials per origin.

    Returns:
        Mapping of source (``studio``, ``gauntlet``, ``calibration``...) to trial count.
    """
    return {r["source"]: int(r["n"]) for r in db.rows("SELECT source, count(*) AS n FROM trials GROUP BY source")}


def questions(include_calibration: bool = False) -> list[dict[str, Any]]:
    """Research questions with their trial counts.

    Args:
        include_calibration: Also list the simulated questions created by gauntlet calibration.

    Returns:
        One dict per question: ``question, n_trials, best_sharpe, best_edge_sharpe, first_trial, last_trial``,
        most recent first.
    """
    where = "" if include_calibration else "WHERE source NOT IN ('calibration')"
    return db.rows(
        f"""SELECT question, count(*) AS n_trials, max(sharpe) AS best_sharpe,
                   max(edge_sharpe) AS best_edge_sharpe, min(created_at) AS first_trial,
                   max(created_at) AS last_trial
            FROM trials {where} GROUP BY question ORDER BY last_trial DESC"""
    )
