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

from . import db
from .timeutil import naive_utc


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
        question: Research question the trial belongs to, e.g. ``"trend:SPY"``. N is counted per question.
        family: Strategy family, e.g. ``"trend_sma"``. The lockbox is tracked per family.
        strategy: Strategy name within the family.
        params: Parameters used, JSON-serialisable.
        symbol: Instrument tested.
        start: First date of the tested period.
        end: Last date of the tested period.
        source: Where the trial came from: ``studio``, ``gauntlet``, ``mining``, ``calibration``, ``demo``.
        metrics: Output of ``backtest.metrics.summary``. Sharpe, CAGR and max drawdown are also stored in
            their own columns for fast queries.
        data_hash: Fingerprint of the input data, so results can be traced to a data version.

    Returns:
        A 12-character hex trial id.
    """
    trial_id = uuid.uuid4().hex[:12]
    with db.connect() as con:
        con.execute(
            """INSERT INTO trials (trial_id, created_at, question, family, strategy, params, symbol,
                   start_date, end_date, source, sharpe, cagr, max_dd, n_obs, metrics, data_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                trial_id,
                naive_utc(),
                question,
                family,
                strategy,
                db.dumps(params),
                symbol,
                str(start)[:10] if start else None,
                str(end)[:10] if end else None,
                source,
                _finite(metrics.get("sharpe")),
                _finite(metrics.get("cagr")),
                _finite(metrics.get("max_dd")),
                int(metrics.get("n_obs") or 0),
                db.dumps(metrics),
                data_hash,
            ],
        )
    return trial_id


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


def list_trials(limit: int = 500, question: str | None = None) -> list[dict[str, Any]]:
    """Recent trials, newest first, with params and metrics decoded.

    Args:
        limit: Maximum number of trials.
        question: Restrict to one research question. None returns all.

    Returns:
        Trial dicts as stored, with ``params`` and ``metrics`` as dicts.
    """
    if question:
        recs = db.rows(
            "SELECT * FROM trials WHERE question = ? ORDER BY created_at DESC LIMIT ?", [question, limit]
        )
    else:
        recs = db.rows("SELECT * FROM trials ORDER BY created_at DESC LIMIT ?", [limit])
    for r in recs:
        r["params"] = json.loads(r["params"] or "{}")
        r["metrics"] = json.loads(r["metrics"] or "{}")
    return recs


def questions() -> list[dict[str, Any]]:
    """Research questions with their trial counts.

    Returns:
        One dict per question: ``question, n_trials, best_sharpe, last_trial``, most recent first.
    """
    return db.rows(
        """SELECT question, count(*) AS n_trials, max(sharpe) AS best_sharpe,
                  max(created_at) AS last_trial
           FROM trials GROUP BY question ORDER BY last_trial DESC"""
    )
