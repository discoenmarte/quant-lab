"""The nightly job: ingest the free sources, record the book's decisions for the latest close, then score the book.

Design:

- **One job, two ways to reach the database.** DuckDB allows one writing process, and a running ``qlab serve`` holds
  it. When the Lab API answers, the job asks the server to do each step (``POST /api/data/ingest``, then
  ``/api/paper/run`` and ``/api/paper/score``); otherwise it runs the same functions in this process.
- **Every step runs even if an earlier one fails.** A failed ingest leaves stale data, which the run refuses on its
  own (see ``book.decide``), and scoring still updates the reports.
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
    return {"ingest": ingest, "run": run, "score": score}


def _in_process(log: Callable[[str], None]) -> dict[str, Any]:
    """Run the three steps in this process.

    Args:
        log: Receives progress lines.

    Returns:
        ``ingest`` (per-source results), ``run`` and ``score`` (per-entry results).
    """
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
    return {"ingest": results, "run": book.run(actor="nightly"), "score": score.score(actor="nightly")}


def nightly(api: str = DEFAULT_API, log: Callable[[str], None] = print, poll: float = 5.0,
            timeout: float = 1800.0) -> dict[str, Any]:
    """Ingest, decide and score, through the API when the server is running.

    Args:
        api: Base URL of the Lab API.
        log: Receives progress lines.
        poll: Seconds between ingest status checks (API mode).
        timeout: Most seconds to wait for the ingest (API mode).

    Returns:
        ``mode`` (``"api"`` or ``"local"``), ``ingest``, ``run``, ``score`` and ``ok`` (False when a source failed
        completely, or an entry was refused or failed).
    """
    mode = "api" if api_up(api) else "local"
    log(f"nightly paper job ({mode})")
    out = _via_api(api, log, poll, timeout) if mode == "api" else _in_process(log)
    bad_ingest = any(r["status"] == "error" for r in out["ingest"])
    bad_book = any(r["status"] in ("refused", "error") for r in out["run"] + out["score"])
    return {"mode": mode, **out, "ok": not (bad_ingest or bad_book)}


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
