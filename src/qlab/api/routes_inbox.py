"""Research inbox endpoints: papers with their rubric scores, specs and their review, replication, the digest and the
model cost meter.

Fetching and triage take minutes with a local model, so ``POST /api/inbox/run`` starts a background job; drafting
one spec, replicating and judging run in the request. Only the owner's requests approve, replicate or judge.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..inbox import digest, replicate, spec, triage
from ..inbox.llm import LLMError
from ..timeutil import now_utc

router = APIRouter(prefix="/inbox")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    """Body of ``POST /api/inbox/run``.

    Attributes:
        max_papers: Most papers to fetch from arXiv.
        triage_limit: Most papers to triage.
        spec_min_score: Rubric score needed for a draft spec.
        spec_limit: Most specs to draft.
    """

    max_papers: int = 100
    triage_limit: int = 100
    spec_min_score: int = 5
    spec_limit: int = 3


class ReviewRequest(BaseModel):
    """Body of ``POST /api/inbox/specs/{spec_id}/review``.

    Attributes:
        decision: ``approve`` or ``reject``.
        reason: Why (required).
        edits: Fields to change before approving (``template``, ``target``, ``params``...).
    """

    decision: str
    reason: str = ""
    edits: dict[str, Any] = Field(default_factory=dict)


def _guard(fn, *args, **kwargs) -> Any:
    """Call an inbox function and turn its expected errors into HTTP errors.

    Args:
        fn: Function.
        *args: Arguments.
        **kwargs: Keyword arguments.

    Returns:
        Its result.

    Raises:
        HTTPException: 404 unknown paper or spec, 400 refused, 503 when the model isn't available.
    """
    try:
        return fn(*args, **kwargs)
    except KeyError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except LLMError as err:
        raise HTTPException(status_code=503, detail=str(err)) from err


def _job(job_id: str, req: RunRequest) -> None:
    """Background body of one inbox run.

    Args:
        job_id: Key into ``_jobs``.
        req: What to run.
    """
    job = _jobs[job_id]

    def log(msg: str) -> None:
        job["log"].append(f"{now_utc().strftime('%H:%M:%S')} {msg}")

    try:
        out = digest.run(req.max_papers, req.triage_limit, req.spec_min_score, req.spec_limit, actor="web", log=log)
        job.update(status="done", digest=out["digest"]["headline"])
    except Exception as err:  # surfaced in the UI instead of dying silently
        job["status"] = "failed"
        log(f"job failed: {err}")
    finally:
        job["finished_at"] = now_utc().isoformat(timespec="seconds")


@router.post("/run")
def run(req: RunRequest) -> dict[str, Any]:
    """Start a background inbox run: fetch, triage, draft specs, digest.

    Args:
        req: Sizes of the run.

    Returns:
        The job.

    Raises:
        HTTPException: 409 while another run is going.
    """
    with _lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            raise HTTPException(status_code=409, detail="an inbox run is already going")
        job_id = uuid.uuid4().hex[:8]
        _jobs[job_id] = {"id": job_id, "status": "running", "started_at": now_utc().isoformat(timespec="seconds"),
                         "finished_at": None, "log": [], "digest": None}
    threading.Thread(target=_job, args=(job_id, req), daemon=True).start()
    return _jobs[job_id]


@router.get("/jobs")
def jobs() -> list[dict[str, Any]]:
    """Recent inbox runs, newest first.

    Returns:
        Jobs with their log.
    """
    return sorted(_jobs.values(), key=lambda j: j["started_at"], reverse=True)[:10]


@router.get("/papers")
def papers(min_score: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    """Papers with their latest rubric verdict, best first.

    Args:
        min_score: Only papers scoring at least this (0 includes untriaged ones).
        limit: Most rows.

    Returns:
        See ``inbox.triage.latest``.
    """
    return triage.latest(min_score, limit)


@router.get("/papers/{arxiv_id}")
def paper(arxiv_id: str) -> dict[str, Any]:
    """One paper with every triage verdict and its specs.

    Args:
        arxiv_id: Paper id.

    Returns:
        The paper row plus ``triage`` (history) and ``specs``.

    Raises:
        HTTPException: 404 for an unknown paper.
    """
    rows = db.rows("SELECT * FROM papers WHERE arxiv_id = ?", [arxiv_id])
    if not rows:
        raise HTTPException(status_code=404, detail=f"no paper {arxiv_id}")
    hist = db.rows("SELECT * FROM paper_triage WHERE arxiv_id = ? ORDER BY triaged_at DESC", [arxiv_id])
    return {**rows[0], "triage": hist, "specs": [s for s in spec.specs() if s["arxiv_id"] == arxiv_id]}


@router.post("/papers/{arxiv_id}/spec")
def draft(arxiv_id: str) -> dict[str, Any]:
    """Draft a spec for one paper now (takes a few seconds to a minute with a local model).

    Args:
        arxiv_id: Paper id.

    Returns:
        The new spec.
    """
    return _guard(spec.draft, arxiv_id, actor="web")


@router.get("/specs")
def specs(status: str | None = None) -> list[dict[str, Any]]:
    """Specs, newest first.

    Args:
        status: Only this status.

    Returns:
        Spec rows.
    """
    return spec.specs(status)


@router.get("/specs/{spec_id}")
def get_spec(spec_id: str) -> dict[str, Any]:
    """One spec with its paper's abstract.

    Args:
        spec_id: Spec id.

    Returns:
        The spec.
    """
    return _guard(spec.get, spec_id)


@router.post("/specs/{spec_id}/review")
def review(spec_id: str, req: ReviewRequest) -> dict[str, Any]:
    """Approve (optionally with edits) or reject a spec, with a reason.

    Args:
        spec_id: Spec id.
        req: Decision, reason and edits.

    Returns:
        The updated spec.
    """
    return _guard(spec.review, spec_id, req.decision, req.reason, req.edits or None, actor="web")


@router.post("/specs/{spec_id}/replicate")
def replicate_spec(spec_id: str) -> dict[str, Any]:
    """Replicate an approved spec over the paper's sample and after its publication (ledger trials).

    Args:
        spec_id: Spec id.

    Returns:
        The spec with its results.
    """
    return _guard(replicate.replicate, spec_id, actor="web")


@router.post("/specs/{spec_id}/judge")
def judge(spec_id: str) -> dict[str, Any]:
    """Send an approved spec through the gauntlet.

    Args:
        spec_id: Spec id.

    Returns:
        The spec with its card id.
    """
    return _guard(replicate.judge, spec_id, actor="web")


@router.get("/compare")
def compare() -> list[dict[str, Any]]:
    """Claimed, replicated and post-publication Sharpe ratios, for the chart.

    Returns:
        One row per run spec.
    """
    return replicate.compare()


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    """The templates and targets a spec can use, for the review form.

    Returns:
        See ``inbox.spec.catalog``.
    """
    return spec.catalog()


@router.get("/digest")
def latest_digest() -> dict[str, Any] | None:
    """The latest saved digest.

    Returns:
        The digest, or null.
    """
    return digest.latest_digest()


@router.post("/digest")
def make_digest(days: int = 7) -> dict[str, Any]:
    """Build and send a digest of the last ``days``.

    Args:
        days: Length of the period.

    Returns:
        The digest.
    """
    return digest.digest(days, actor="web")


@router.get("/usage")
def usage(days: int = 30) -> dict[str, Any]:
    """The model cost meter.

    Args:
        days: Window.

    Returns:
        See ``inbox.digest.usage``.
    """
    return digest.usage(days)
