"""Daily report and alert endpoints: build a report, read the latest or an older one, the alert log and channels.

Alerts only inform. No endpoint here changes a position, the paper book or a limit.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import alerts, report

router = APIRouter()


class ReportRequest(BaseModel):
    """Body of ``POST /api/report``.

    Attributes:
        trigger: Who asks: ``web`` (the Strategy Board), ``cli`` or ``nightly`` (the nightly job in API mode).
        nightly: The nightly job's summary, when the job asks.
        digest: Also send the headline as one message through the channels.
        notify: False delivers alerts to the file channel only.
    """

    trigger: Literal["web", "cli", "nightly"] = "web"
    nightly: dict[str, Any] | None = None
    digest: bool = False
    notify: bool = True


@router.post("/report")
def build_report(req: ReportRequest) -> dict[str, Any]:
    """Build a report covering the time since the previous one, and raise its alerts.

    Args:
        req: Trigger and options.

    Returns:
        The report (see ``report.build``).
    """
    actor = "nightly" if req.trigger == "nightly" else req.trigger
    return report.build(trigger=req.trigger, nightly=req.nightly, notify=req.notify, send_digest=req.digest,
                        actor=actor)


@router.get("/report/latest")
def latest_report() -> dict[str, Any] | None:
    """The newest report.

    Returns:
        The report, or null before the first one.
    """
    return report.latest()


@router.get("/report/{report_id}")
def get_report(report_id: str) -> dict[str, Any]:
    """One saved report.

    Args:
        report_id: Report id.

    Returns:
        The report.

    Raises:
        HTTPException: 404 for an unknown report.
    """
    try:
        return report.load(report_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.get("/reports")
def list_reports(limit: int = 30) -> list[dict[str, Any]]:
    """Saved reports, newest first.

    Args:
        limit: Maximum number of reports.

    Returns:
        Rows ``report_id, created_at, since, trigger, n_alerts, headline``.
    """
    return report.list_reports(limit)


@router.get("/alerts")
def alert_log(limit: int = 100) -> list[dict[str, Any]]:
    """The alert log, newest first.

    Args:
        limit: Maximum number of alerts.

    Returns:
        Alert rows with their delivery per channel.
    """
    return alerts.recent(limit)


@router.get("/alerts/channels")
def alert_channels() -> list[dict[str, Any]]:
    """Which channels deliver alerts. Never includes a token.

    Returns:
        Rows ``name, enabled, detail``.
    """
    return alerts.channel_status()


@router.post("/alerts/test")
def test_alert() -> dict[str, Any]:
    """Send a test alert through every channel.

    Returns:
        The stored alert, with what each channel did.
    """
    return alerts.send_test(actor="web")
