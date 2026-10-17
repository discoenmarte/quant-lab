"""Core endpoints: health, system info, overview, audit log, glossary and the demo pipeline."""

from __future__ import annotations

import platform
import time
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from fastapi import APIRouter

from .. import __version__, audit, config, db, ledger
from ..backtest import metrics
from ..glossary import GLOSSARY
from ..timeutil import clocks

router = APIRouter()

MILESTONES = [
    {"id": "M0", "name": "Foundations", "status": "done"},
    {"id": "M1", "name": "Data lake", "status": "done"},
    {"id": "M2", "name": "Backtest engine", "status": "done"},
    {"id": "M3", "name": "Validation gauntlet", "status": "in progress"},
    {"id": "M4", "name": "Research inbox", "status": "planned"},
    {"id": "M5", "name": "Strategy mining", "status": "planned"},
    {"id": "M6", "name": "Registry, portfolio, alerts", "status": "planned"},
    {"id": "M7", "name": "Paper trading", "status": "planned"},
    {"id": "M8", "name": "Go-live gate", "status": "planned"},
    {"id": "M9", "name": "Intraday mode", "status": "planned"},
]


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness check used by the web app's status dot.

    Returns:
        ``ok``, the lab version and the current UTC and exchange times.
    """
    return {"ok": True, "version": __version__, **clocks()}


@router.get("/system")
def system() -> dict[str, Any]:
    """Versions, paths, database size, record counts and links to companion services.

    Returns:
        A dict rendered by the System page.
    """
    dbfile = config.db_path()
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "libraries": {"duckdb": duckdb.__version__, "pandas": pd.__version__, "numpy": np.__version__},
        "paths": {"data": str(config.data_dir()), "database": str(dbfile)},
        "database_mb": round(dbfile.stat().st_size / 1e6, 2) if dbfile.exists() else 0.0,
        "counts": _counts(),
        "services": [
            {"name": "Lab API docs", "url": "/docs"},
            {"name": "Dagster (run `make dagster`)", "url": "http://127.0.0.1:3000"},
        ],
        **clocks(),
    }


def _counts() -> dict[str, int]:
    """Row counts of the main tables.

    Returns:
        Counts of trials, research questions, data series, sources, gauntlet cards and audit events.
    """
    with db.connect() as con:

        def one(sql: str) -> int:
            return int(con.execute(sql).fetchone()[0] or 0)

        return {
            "trials": one("SELECT count(*) FROM trials"),
            "questions": one("SELECT count(DISTINCT question) FROM trials"),
            "series": one("SELECT count(*) FROM series"),
            "sources": one("SELECT count(*) FROM sources"),
            "gauntlet_cards": one("SELECT count(*) FROM gauntlet_cards"),
            "audit_events": one("SELECT count(*) FROM audit"),
        }


@router.get("/overview")
def overview() -> dict[str, Any]:
    """Everything the Home page needs in one call.

    Returns:
        Milestone statuses, record counts, the latest audit events and the most active research questions.
    """
    return {
        "milestones": MILESTONES,
        "counts": _counts(),
        "recent_audit": audit.recent(8),
        "questions": ledger.questions()[:5],
        **clocks(),
    }


@router.get("/audit")
def audit_list(limit: int = 200) -> list[dict[str, Any]]:
    """Latest audit events, newest first.

    Args:
        limit: Maximum number of events.

    Returns:
        Audit event dicts.
    """
    return audit.recent(limit)


@router.get("/glossary")
def glossary() -> dict[str, dict[str, str]]:
    """Plain-language definitions used by the UI's info tooltips.

    Returns:
        Mapping of glossary key to ``{"term", "short", "long"}``.
    """
    return GLOSSARY


@router.post("/demo")
def run_demo() -> dict[str, Any]:
    """Run a tiny end-to-end pipeline: synthetic prices, metrics, ledger, audit log.

    Every run is a new trial on the same research question, so the trial count N visibly rises. That habit,
    counting every attempt, is what the whole lab is built on.

    Returns:
        The trial id, the question's new trial count, the metrics, and timings for each step.
    """
    steps: list[dict[str, Any]] = []

    t = time.perf_counter()
    rng = np.random.default_rng()
    idx = pd.bdate_range("2016-01-01", periods=2520)
    rets = pd.Series(rng.standard_t(df=4, size=len(idx)) * 0.007 + 0.0002, index=idx)
    steps.append({"step": "Generate 10 years of synthetic daily returns", "ms": _ms(t)})

    t = time.perf_counter()
    m = metrics.summary(rets)
    steps.append({"step": "Compute metrics", "ms": _ms(t)})

    t = time.perf_counter()
    question = "demo:synthetic-buy-and-hold"
    trial_id = ledger.record_trial(
        question=question,
        family="demo",
        strategy="buy_and_hold",
        params={},
        symbol="SYNTH",
        start=idx[0].date(),
        end=idx[-1].date(),
        source="demo",
        metrics=m,
    )
    steps.append({"step": f"Record trial {trial_id} in the ledger", "ms": _ms(t)})

    t = time.perf_counter()
    audit.log("demo.run", target=trial_id, details={"sharpe": m["sharpe"]})
    steps.append({"step": "Write the audit log", "ms": _ms(t)})

    return {
        "trial_id": trial_id,
        "question": question,
        "n_trials": ledger.count_trials(question),
        "metrics": m,
        "steps": steps,
    }


def _ms(t0: float) -> float:
    """Milliseconds elapsed since a ``time.perf_counter()`` reading.

    Args:
        t0: Earlier ``perf_counter`` value.

    Returns:
        Elapsed time in milliseconds, rounded to 0.01.
    """
    return round((time.perf_counter() - t0) * 1000, 2)
