"""Mining endpoints: the grammar, jobs (queued on one background runner), budgets, proposals and the gauntlet hand-off.

Jobs run one at a time in this server process, so the lab keeps a single DuckDB writer; starting one from the CLI
while the server is up sends it here (``qlab mine run``).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..data import store
from ..gauntlet import calibration
from ..mining import formula, grammar, jobs
from ..mining import search as searching
from ..portfolio.panel import UNIVERSES

router = APIRouter(prefix="/mining")


class JobRequest(BaseModel):
    """Body of ``POST /api/mining/jobs``.

    Attributes:
        kind: ``bars`` (one instrument) or ``panel`` (a universe).
        name: Instrument or universe.
        source: Data source of an instrument (``yahoo``, ``binance``).
        paradigm: ``random``, ``tpe``, ``gp`` or ``llm``.
        max_trials: Trials this job may evaluate, capped by the question's budget.
        max_minutes: Wall-clock limit.
        seed: Random seed.
        workers: Evaluation processes.
        banned: Operators, terminals, rules or components to leave out.
        hint: Research direction for the language model.
        note: Why this job.
    """

    kind: Literal["bars", "panel"] = "bars"
    name: str = "SPY"
    source: str = ""
    paradigm: str = "gp"
    max_trials: int = Field(500, ge=1, le=100_000)
    max_minutes: float = Field(30.0, gt=0, le=24 * 60)
    seed: int = 1
    workers: int = Field(4, ge=1, le=32)
    banned: list[str] = Field(default_factory=list)
    hint: str = ""
    note: str = ""


class GauntletRequest(BaseModel):
    """Body of ``POST /api/mining/jobs/{job_id}/gauntlet``.

    Attributes:
        strategy: A ``mined_...`` name from the job's candidates.
        seed: Gauntlet seed.
    """

    strategy: str
    seed: int = 7


class BudgetRequest(BaseModel):
    """Body of ``POST /api/mining/budgets``.

    Attributes:
        question: ``mined:<scope>:1d``.
        max_trials: New cap on its mining trials.
        reason: Why, in plain words.
    """

    question: str
    max_trials: int = Field(ge=0, le=10_000_000)
    reason: str


class ProposeRequest(BaseModel):
    """Body of ``POST /api/mining/propose``.

    Attributes:
        kind: ``bars`` or ``panel``.
        name: Instrument or universe.
        source: Data source of an instrument.
        n: Proposals wanted.
        hint: Research direction.
    """

    kind: Literal["bars", "panel"] = "bars"
    name: str = "SPY"
    source: str = ""
    n: int = Field(5, ge=1, le=20)
    hint: str = ""


def _scope(kind: str, name: str, source: str) -> jobs.Scope:
    """A checked scope, as an HTTP error when it isn't valid.

    Args:
        kind: ``bars`` or ``panel``.
        name: Instrument or universe.
        source: Data source of an instrument.

    Returns:
        The scope.

    Raises:
        HTTPException: 400 for an unknown universe or a missing source.
    """
    try:
        if kind == "panel":
            return jobs.make_scope("panel", name)
        return jobs.make_scope("bars", name, source or ("binance" if name.endswith("USDT") else "yahoo"))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/grammar")
def grammar_info() -> dict[str, Any]:
    """What the miner can build and where it can search.

    Returns:
        ``terminals`` (with plain words), ``operators`` by family, ``windows``, limits, ``components``, ``rules`` by
        scope kind, ``paradigms``, the default budget, ``universes`` and ``instruments`` (daily series ingested).
    """
    series = [s for s in store.list_series() if s.get("interval") == "1d" and s.get("source") in ("yahoo", "binance")
              and (s.get("kind") or "bars") == "bars"]
    return {
        "terminals": formula.TERMINALS,
        "operators": {"unary": formula.UNARY, "binary": formula.BINARY, "time_series": formula.TS,
                      "two_series": formula.TS2, "cross_section": formula.CS},
        "windows": formula.WINDOWS, "max_nodes": formula.MAX_NODES, "max_depth": formula.MAX_DEPTH,
        "components": grammar.COMPONENTS,
        "rules": {"bars": grammar.BAR_RULES, "static": grammar.STATIC_RULES, "dynamic": grammar.DYNAMIC_RULES},
        "paradigms": searching.PARADIGMS, "default_budget": jobs.DEFAULT_BUDGET,
        "universes": [{"name": k, "description": v["description"]} for k, v in UNIVERSES.items()],
        "instruments": sorted({(s["source"], s["symbol"]) for s in series if s["source"] == "yahoo"
                               or s["symbol"] in ("BTCUSDT", "ETHUSDT")}),
    }


@router.get("/jobs")
def list_jobs(limit: int = 100) -> list[dict[str, Any]]:
    """Recent jobs, newest first.

    Args:
        limit: Most rows.

    Returns:
        Job rows.
    """
    return jobs.jobs(limit)


@router.post("/jobs")
def start_job(req: JobRequest) -> dict[str, Any]:
    """Queue a mining job on the background runner.

    Args:
        req: The job.

    Returns:
        The job row.

    Raises:
        HTTPException: 400 for an invalid scope, paradigm, budget or ban list.
    """
    scope = _scope(req.kind, req.name, req.source)
    try:
        row = jobs.create(scope, req.paradigm, req.max_trials, req.max_minutes, req.seed, req.workers, req.banned,
                          req.hint, req.note, actor="user")
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    jobs.submit(row["job_id"])
    return row


@router.get("/jobs/{job_id}")
def job_report(job_id: str) -> dict[str, Any]:
    """One job's report, live while it runs.

    Args:
        job_id: Job id.

    Returns:
        The report.

    Raises:
        HTTPException: 404 for an unknown job.
    """
    try:
        return jobs.load(job_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: str) -> dict[str, Any]:
    """Stop a queued or running job after its current batch.

    Args:
        job_id: Job id.

    Returns:
        ``{"stopping": True}``.

    Raises:
        HTTPException: 404 for an unknown job, 409 when it isn't running or queued.
    """
    try:
        ok = jobs.stop(job_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    if not ok:
        raise HTTPException(status_code=409, detail="this job isn't running or queued in this server")
    return {"stopping": True}


@router.post("/jobs/{job_id}/gauntlet")
def gauntlet(job_id: str, req: GauntletRequest) -> dict[str, Any]:
    """Judge one of a job's candidates with the gauntlet. Takes seconds for one instrument, minutes for crypto.

    Args:
        job_id: Job id.
        req: Candidate and seed.

    Returns:
        The report card.

    Raises:
        HTTPException: 404 for an unknown job or candidate, 400 when the gauntlet refuses it.
    """
    try:
        return jobs.send_to_gauntlet(job_id, req.strategy, req.seed, actor="user")
    except (FileNotFoundError, KeyError) as err:
        raise HTTPException(status_code=404, detail=str(err).strip("'")) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/budgets")
def list_budgets() -> list[dict[str, Any]]:
    """Every mining question's trial budget and use.

    Returns:
        Budget rows.
    """
    return jobs.budgets()


@router.post("/budgets")
def set_budget(req: BudgetRequest) -> dict[str, Any]:
    """Change a mining question's trial budget (audited, with a reason).

    Args:
        req: Question, cap and reason.

    Returns:
        The budget after the change.

    Raises:
        HTTPException: 400 for a non-mining question or a missing reason.
    """
    try:
        return jobs.set_budget(req.question, req.max_trials, req.reason, actor="user")
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/propose")
def propose(req: ProposeRequest) -> dict[str, Any]:
    """Ask the local model for ideas with rationales, validated against the grammar. Runs no backtest.

    Args:
        req: Scope, count and research direction.

    Returns:
        ``proposals`` (``text``, ``spec``, ``rationale``), ``outside`` (ideas the grammar can't express),
        ``rejected`` and ``calls``.

    Raises:
        HTTPException: 400 for an invalid scope, 404 when its data isn't ingested, 503 when the model fails.
    """
    from ..inbox.llm import LLMError
    from ..mining.evaluate import Evaluator
    from ..mining.llm import Proposer, describe_scope

    scope = _scope(req.kind, req.name, req.source)
    try:
        space = Evaluator.for_scope(scope, leak_cuts=0).space
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    prop = Proposer(describe_scope(scope, space), hint=req.hint)
    try:
        got = prop.propose(space, req.n)
    except LLMError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err
    return {"proposals": [{"text": grammar.describe(s), "spec": s, "rationale": why} for s, why in got],
            "outside": prop.outside, "rejected": prop.rejected, "calls": prop.calls}


@router.get("/calibration")
def mining_calibration() -> dict[str, Any] | None:
    """The latest mining calibration study, without per-run detail.

    Returns:
        The report, or None when no study has run.
    """
    for rep in calibration.list_reports():
        if rep.get("kind") == "mining":
            return rep
    return None
