"""The trial ledger: every backtest the lab runs, from any source, is recorded here.

The ledger is what keeps the statistics honest. The Deflated Sharpe Ratio needs the number of trials (N)
and the spread of Sharpe ratios across them, per research question. Nothing gets to skip the ledger,
including manual runs from the Backtest Studio.
"""

from __future__ import annotations

import json
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
                _num(metrics.get("sharpe")),
                _num(metrics.get("cagr")),
                _num(metrics.get("max_dd")),
                int(metrics.get("n_obs") or 0),
                db.dumps(metrics),
                data_hash,
            ],
        )
    return trial_id


def _num(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def count_trials(question: str | None = None) -> int:
    if question is None:
        return int(db.scalar("SELECT count(*) FROM trials") or 0)
    return int(db.scalar("SELECT count(*) FROM trials WHERE question = ?", [question]) or 0)


def sharpes(question: str) -> list[float]:
    """Annualised Sharpe ratios of every trial on a research question (inputs to the DSR)."""
    recs = db.rows("SELECT sharpe FROM trials WHERE question = ? AND sharpe IS NOT NULL", [question])
    return [float(r["sharpe"]) for r in recs]


def list_trials(limit: int = 500, question: str | None = None) -> list[dict[str, Any]]:
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
    return db.rows(
        """SELECT question, count(*) AS n_trials, max(sharpe) AS best_sharpe,
                  max(created_at) AS last_trial
           FROM trials GROUP BY question ORDER BY last_trial DESC"""
    )
