"""The nightly job: ingest the free sources, record the book's decisions for the latest close, score the book, and
build the daily report.

Design:

- **One job, two ways to reach the database.** DuckDB allows one writing process, and a running ``qlab serve`` holds
  it. When the Lab API answers, the job asks the server to do each step (``POST /api/data/ingest``, then
  ``/api/paper/run`` and ``/api/paper/score``); otherwise it runs the same functions in this process.
- **Every step runs even if an earlier one fails.** A failed ingest leaves stale data, which the run refuses on its
  own (see ``book.decide``), and scoring still updates the reports.
- **Broker paper trading comes after scoring** (M7b, decision 0009): fetch the fills of earlier orders and reconcile,
  then send each enrolled sleeve's new decision to its paper broker within the limits. With no sleeves it does
  nothing; a broker that can't be reached is reported without stopping the job.
- **The daily report comes last** (``qlab.report``, decision 0008). It gets the job's summary, records it in the
  audit log, sends the headline as a digest and raises an alert if any step failed. A step that crashes still gets a
  report. If the report itself fails (a bug, or a server that went away), the failure alert goes straight to the
  alert channels without the database, so a broken night is never silent.
- **Scheduling is the owner's choice.** ``render_launchd`` writes a launchd job from the template in
  ``ops/launchd`` and returns the install steps; nothing here installs it.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .. import config
from ..config import EXCHANGE_TZ
from ..timeutil import now_utc

DEFAULT_API = "http://127.0.0.1:8765/api"
LABEL = "com.quantlab.paper-nightly"
TEMPLATE = config.ROOT / "ops" / "launchd" / f"{LABEL}.plist.template"
NY_RUN_TIME = (19, 0)  # 19:00 New York: 2.5 hours after the 16:30 final close, 14.5 hours before the next open


def api_up(api: str) -> bool:
    """Whether the Lab API answers.

    Args:
        api: Base URL of the API, e.g. ``http://127.0.0.1:8765/api``.

    Returns:
        True when ``/health`` returns ok.
    """
    try:
        return httpx.get(f"{api}/health", timeout=3).json().get("ok") is True
    except (httpx.HTTPError, ValueError):
        return False


def _via_api(api: str, log: Callable[[str], None], poll: float, timeout: float) -> dict[str, Any]:
    """Run the three steps through a running Lab API.

    Args:
        api: Base URL of the API.
        log: Receives progress lines.
        poll: Seconds between ingest status checks.
        timeout: Most seconds to wait for the ingest.

    Returns:
        ``ingest`` (per-source results), ``run`` and ``score`` (per-entry results).
    """
    with httpx.Client(base_url=api, timeout=120) as client:
        res = client.post("/data/ingest", json={})
        if res.status_code == 409:
            log("an ingest is already running; waiting for it")
            job_id = None
        else:
            res.raise_for_status()
            job_id = res.json()["id"]
        started, job = time.monotonic(), None
        while time.monotonic() - started < timeout:
            jobs = client.get("/data/jobs").json()
            job = next((j for j in jobs if j["id"] == job_id), None) if job_id else (jobs[0] if jobs else None)
            if job is None or job["status"] != "running":
                break
            time.sleep(poll)
        ingest = (job or {}).get("results", [])
        if job and job["status"] == "running":
            log(f"ingest still running after {timeout:.0f} s; deciding on the data stored so far")
        run = client.post("/paper/run").json()
        score = client.post("/paper/score").json()
        broker = {"sync": client.post("/broker/sync").json(), "execute": client.post("/broker/execute").json()}
    return {"ingest": ingest, "run": run, "score": score, "broker": broker}


def _in_process(log: Callable[[str], None]) -> dict[str, Any]:
    """Run the three steps in this process.

    Args:
        log: Receives progress lines.

    Returns:
        ``ingest`` (per-source results), ``run`` and ``score`` (per-entry results) and ``broker`` (``sync`` and
        ``execute``).
    """
    from ..broker import execution
    from ..data.ingest import SOURCES, ingest
    from . import book, score

    results = []
    for name in SOURCES:
        try:
            res = ingest(name, log=log, actor="nightly")
            results.append({"source": name, "status": res["status"], "n_series": len(res["series"]),
                            "errors": res["errors"]})
        except Exception as err:  # one failed source must not stop the decisions
            results.append({"source": name, "status": "error", "n_series": 0, "errors": [str(err)]})
    run, scored = book.run(actor="nightly"), score.score(actor="nightly")
    broker = {"sync": execution.sync(), "execute": execution.execute(actor="nightly")}
    return {"ingest": results, "run": run, "score": scored, "broker": broker}


def summary(res: dict[str, Any], started: datetime, finished: datetime) -> dict[str, Any]:
    """A compact record of one night's run, for the daily report and the audit log.

    Args:
        res: The job's result so far (``mode``, ``ingest``, ``run``, ``score``, ``ok`` and maybe ``error``).
        started: When the job started (tz-aware).
        finished: When its steps finished (tz-aware).

    Returns:
        ``ok, mode, started_at, finished_at, error``, ``ingest`` (per source: status, series and the first errors),
        ``run`` (counts per decision status and the refusal messages), ``score`` (counts and entries in alarm) and
        ``broker`` (sleeves per status, new fills, accounts that don't reconcile, errors).
    """
    run, score = res.get("run", []), res.get("score", [])
    broker = res.get("broker") or {}
    executed = broker.get("execute") or []
    synced = broker.get("sync") or {}
    return {
        "ok": bool(res["ok"]), "mode": res["mode"], "error": res.get("error"),
        "started_at": started.isoformat(timespec="seconds"), "finished_at": finished.isoformat(timespec="seconds"),
        "ingest": [{"source": r["source"], "status": r["status"], "n_series": r.get("n_series", 0),
                    "errors": [str(e) for e in r.get("errors", [])][:3]} for r in res.get("ingest", [])],
        "run": {**{k: sum(r["status"] == k for r in run) for k in ("decided", "exists", "refused", "error")},
                "messages": [r["message"] for r in run if r["status"] in ("refused", "error")][:5]},
        "score": {"scored": sum(r["status"] == "scored" for r in score),
                  "error": sum(r["status"] == "error" for r in score),
                  "alarms": [r["entry_id"] for r in score if r.get("alarm")]},
        "broker": {**{k: sum(r["status"] == k for r in executed)
                      for k in ("sent", "exists", "blocked", "not_configured", "too_late", "error")},
                   "fills": synced.get("fills", 0),
                   "mismatches": [k for k, v in (synced.get("reconciliation") or {}).items() if not v.get("ok")],
                   "errors": [str(e) for e in synced.get("errors", [])][:3]},
    }


def _report(mode: str, api: str, summ: dict[str, Any]) -> dict[str, Any]:
    """Build the daily report with the job's summary: through the API in API mode, here otherwise.

    Args:
        mode: ``"api"`` or ``"local"``.
        api: Base URL of the API.
        summ: Output of ``summary``.

    Returns:
        ``report_id``, ``headline`` and ``alerts`` (rule, title and delivery of each alert raised).
    """
    if mode == "api":
        with httpx.Client(base_url=api, timeout=120) as client:
            res = client.post("/report", json={"trigger": "nightly", "nightly": summ, "digest": True})
            res.raise_for_status()
            rep = res.json()
    else:
        from .. import report

        rep = report.build(trigger="nightly", nightly=summ, send_digest=True, actor="nightly")
    return {"report_id": rep["report_id"], "headline": rep["headline"],
            "alerts": [{k: a[k] for k in ("rule", "severity", "title", "delivery")} for a in rep["alerts"]]}


def nightly(api: str = DEFAULT_API, log: Callable[[str], None] = print, poll: float = 5.0,
            timeout: float = 1800.0, report: bool = True) -> dict[str, Any]:
    """Ingest, decide, score and report, through the API when the server is running.

    Args:
        api: Base URL of the Lab API.
        log: Receives progress lines.
        poll: Seconds between ingest status checks (API mode).
        timeout: Most seconds to wait for the ingest (API mode).
        report: Build the daily report and raise its alerts at the end.

    Returns:
        ``mode`` (``"api"`` or ``"local"``), ``ingest``, ``run``, ``score``, ``ok`` (False when a source failed
        completely, an entry was refused or failed, a step crashed or the report failed), ``error`` when a step
        crashed, and ``report`` (``report_id``, ``headline``, ``alerts``, or ``error``) unless ``report`` is False.
    """
    started = now_utc()
    mode = "api" if api_up(api) else "local"
    log(f"nightly paper job ({mode})")
    error = None
    try:
        out = _via_api(api, log, poll, timeout) if mode == "api" else _in_process(log)
    except Exception as err:  # a crashed step must still end in a report and an alert
        out, error = {"ingest": [], "run": [], "score": [], "broker": {}}, f"{type(err).__name__}: {err}"
        log(f"the job stopped: {error}")
    bad_ingest = any(r["status"] == "error" for r in out["ingest"])
    bad_book = any(r["status"] in ("refused", "error") for r in out["run"] + out["score"])
    broker = out.get("broker") or {}
    bad_broker = any(r["status"] == "error" for r in broker.get("execute") or []) or any(
        not v.get("ok") for v in ((broker.get("sync") or {}).get("reconciliation") or {}).values())
    res = {"mode": mode, **out, "ok": not (bad_ingest or bad_book or bad_broker or error)}
    if error:
        res["error"] = error
    if report:
        summ = summary(res, started, now_utc())
        try:
            res["report"] = _report(mode, api, summ)
        except Exception as err:  # tell the owner without the database, which may be what failed
            from .. import alerts

            msg = f"{type(err).__name__}: {err}"
            log(f"the daily report failed: {msg}")
            res["report"], res["ok"] = {"error": msg}, False
            alerts.deliver([alerts.nightly_alert({**summ, "ok": False, "report_error": msg},
                                                 f"nightly:{summ['started_at']}")])
    return res


def default_time(tz: str | None = None) -> tuple[int, int]:
    """A local wall-clock time that falls after the New York close in both winter and summer.

    launchd schedules in the machine's local time, and New York's daylight saving needn't match the local one, so
    this takes the later of 19:00 New York's local equivalents on a winter and a summer date.

    Args:
        tz: IANA name of the local timezone; None means the machine's.

    Returns:
        ``(hour, minute)`` in local time.
    """
    local = ZoneInfo(tz) if tz else datetime.now().astimezone().tzinfo
    ny = ZoneInfo(EXCHANGE_TZ)
    times = [datetime(2026, m, 15, *NY_RUN_TIME, tzinfo=ny).astimezone(local) for m in (1, 7)]
    later = max(times, key=lambda t: (t.hour - 12) % 24 * 60 + t.minute)  # order times from noon on
    return later.hour, later.minute


def render_launchd(out_dir: Path, hour: int, minute: int) -> tuple[Path, list[str]]:
    """Write the launchd job for this checkout and list the steps to install it. Installs nothing.

    Args:
        out_dir: Folder for the rendered plist (inside the lab's data folder by default, which git ignores).
        hour: Local hour to run at.
        minute: Local minute.

    Returns:
        ``(plist path, install steps as shell lines)``.

    Raises:
        FileNotFoundError: The template or ``uv`` is missing.
    """
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(uv).exists():
        raise FileNotFoundError("uv is not on PATH; install it or pass its location in the template by hand")
    logs = config.data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    text = (TEMPLATE.read_text()
            .replace("__LABEL__", LABEL).replace("__UV__", uv).replace("__REPO__", str(config.ROOT))
            .replace("__LOG__", str(logs / "paper-nightly.log"))
            .replace("__HOUR__", str(hour)).replace("__MINUTE__", str(minute)))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{LABEL}.plist"
    path.write_text(text)
    agent = f"~/Library/LaunchAgents/{LABEL}.plist"
    steps = [
        "# install (runs every day at the time in the plist; a run missed while asleep happens on wake)",
        f"cp {path} {agent}",
        f"launchctl bootstrap gui/$(id -u) {agent}",
        "# run it once now, and check it",
        f"launchctl kickstart -k gui/$(id -u)/{LABEL}",
        f"tail -n 40 {logs / 'paper-nightly.log'}",
        "# uninstall",
        f"launchctl bootout gui/$(id -u)/{LABEL} && rm {agent}",
    ]
    return path, steps
