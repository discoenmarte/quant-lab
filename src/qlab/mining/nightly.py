"""The nightly mining rotation: one budgeted job a night, judged only when it clears the luck bar.

Until 2026-10-22 this ran as a scheduled Claude session following written steps. On a server it must be a plain
program (decision 0012), so the same steps live here:

1. **Guards.** Mining waits unless the thresholds in force count each mined trial at least twice (decision 0011),
   and skips a night when another job, a gauntlet or a calibration study is already using the machine.
2. **Scope.** The rotation (``QLAB_MINING_SCOPES``, default ``all_etfs`` and ``crypto_top30``, which share no
   assets) mines whichever scope has used the fewest mined trials. A scope whose budget is spent is skipped; budgets
   are never raised here (that needs the owner's written reason).
3. **Paradigm by weekday** in the owner's timezone (``QLAB_OWNER_TZ``, default Bogotá): genetic programming on
   Monday, Thursday and Sunday, random search on Tuesday and Friday, Bayesian (TPE) on Wednesday and Saturday. The
   language-model paradigm is never used: it isn't calibrated.
4. **Run** 250 trials (fewer if the budget has fewer left), seeded with the date, through the running Lab API when it
   answers (it holds the database), otherwise in this process.
5. **Judge only what clears the bar.** The best candidate goes to the gauntlet only if its edge reaches what a
   Deflated Sharpe of 0.95 needs at the scope's N. A pass is research evidence, not a trading decision: nothing
   here touches the paper book or a broker.

The night ends in one info alert with the result, so the owner sees it on Telegram.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .. import alerts, config
from ..timeutil import now_utc

DEFAULT_SCOPES = ("all_etfs", "crypto_top30")
PARADIGMS = {0: "gp", 1: "random", 2: "tpe", 3: "gp", 4: "random", 5: "tpe", 6: "gp"}  # Monday is 0
HEAVY = r"[c]alibrate|[q]lab mine run|[q]lab gauntlet|[q]lab portfolio-gauntlet"
FINAL = ("done", "stopped", "failed", "refused")


def scopes() -> list[str]:
    """The scopes the rotation mines.

    Returns:
        ``QLAB_MINING_SCOPES`` split on commas, or the two default scopes.
    """
    raw = config.setting("QLAB_MINING_SCOPES", "") or ""
    return [s.strip() for s in raw.split(",") if s.strip()] or list(DEFAULT_SCOPES)


def owner_day(now: datetime) -> date:
    """The owner's calendar date at a moment.

    Args:
        now: Tz-aware time.

    Returns:
        The date in ``QLAB_OWNER_TZ`` (default ``America/Bogota``).
    """
    return now.astimezone(ZoneInfo(config.setting("QLAB_OWNER_TZ", "America/Bogota") or "America/Bogota")).date()


def pick(budgets: list[dict[str, Any]], rotation: list[str], day: date) -> dict[str, Any]:
    """Tonight's scope, paradigm, trials and seed.

    Args:
        budgets: Budget rows (``question, used, remaining``); a scope without a row has used nothing.
        rotation: Scopes in order of preference on a tie.
        day: The owner's date.

    Returns:
        ``scope, question, paradigm, trials, seed``, or ``{"skip": reason}`` when every budget is spent.
    """
    rows = {b["question"]: b for b in budgets}
    usable = []
    for i, sc in enumerate(rotation):
        q = f"mined:{sc}:1d"
        b = rows.get(q, {"used": 0, "remaining": 2000})
        if b["remaining"] > 0:
            usable.append((b["used"], i, sc, q, b["remaining"]))
    if not usable:
        return {"skip": "every mining budget in the rotation is spent; raising one needs the owner's written reason"}
    _, _, sc, q, left = min(usable)
    return {"scope": sc, "question": q, "paradigm": PARADIGMS[day.weekday()], "trials": min(250, left),
            "seed": int(day.strftime("%Y%m%d"))}


def busy(running: list[dict[str, Any]]) -> str:
    """Why tonight's run should wait for something else using the machine.

    Args:
        running: Mining jobs that are queued or running.

    Returns:
        A reason, or an empty string when the machine is free.
    """
    if running:
        return f"mining job {running[0]['job_id']} is still {running[0]['status']}"
    try:
        out = subprocess.run(["pgrep", "-fl", HEAVY], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"another study or job is running ({out.splitlines()[0][:80]})" if out else ""


class _Local:
    """The lab in this process. Each method mirrors one step of ``run``."""

    def factor(self) -> float:
        """How many times each mined trial counts in N under the thresholds in force.

        Returns:
            ``mining_n_factor``.
        """
        from ..gauntlet.thresholds import current

        return float(current()[1].get("mining_n_factor", 1.0))

    def budgets(self) -> list[dict[str, Any]]:
        """Every mining question's budget.

        Returns:
            Budget rows.
        """
        from . import jobs

        return jobs.budgets()

    def running(self) -> list[dict[str, Any]]:
        """Mining jobs that are queued or running.

        Returns:
            Job rows.
        """
        from . import jobs

        return [j for j in jobs.jobs(20) if j["status"] in ("queued", "running")]

    def mine(self, plan: dict[str, Any], workers: int, minutes: float, log: Callable[[str], None]) -> dict[str, Any]:
        """Run one job here.

        Args:
            plan: Output of ``pick``.
            workers: Evaluation processes.
            minutes: Time limit.
            log: Receives progress lines.

        Returns:
            The finished job's report.
        """
        from . import jobs

        row = jobs.create(jobs.parse_scope(plan["scope"]), plan["paradigm"], plan["trials"], minutes, plan["seed"],
                          workers, note="nightly rotation", actor="nightly")
        return jobs.run(row["job_id"], log=log)

    def judge(self, job_id: str, strategy: str) -> dict[str, Any]:
        """Send a candidate to the gauntlet.

        Args:
            job_id: Job id.
            strategy: Candidate name.

        Returns:
            Its report card.
        """
        from . import jobs

        return jobs.send_to_gauntlet(job_id, strategy, actor="nightly")


class _Api:
    """The lab behind a running Lab API.

    Args:
        api: Base URL, e.g. ``http://127.0.0.1:8765/api``.
        poll: Seconds between progress checks.
    """

    def __init__(self, api: str, poll: float = 15.0) -> None:
        self.client, self.poll = httpx.Client(base_url=api, timeout=60), poll

    def factor(self) -> float:
        """See ``_Local.factor``.

        Returns:
            ``mining_n_factor``.
        """
        return float(self.client.get("/gauntlet/thresholds").json()["config"].get("mining_n_factor", 1.0))

    def budgets(self) -> list[dict[str, Any]]:
        """See ``_Local.budgets``.

        Returns:
            Budget rows.
        """
        return self.client.get("/mining/budgets").json()

    def running(self) -> list[dict[str, Any]]:
        """See ``_Local.running``.

        Returns:
            Job rows.
        """
        return [j for j in self.client.get("/mining/jobs", params={"limit": 20}).json()
                if j["status"] in ("queued", "running")]

    def mine(self, plan: dict[str, Any], workers: int, minutes: float, log: Callable[[str], None]) -> dict[str, Any]:
        """Queue one job on the server's runner and wait for it (up to its time limit plus 15 minutes).

        Args:
            plan: Output of ``pick``.
            workers: Evaluation processes.
            minutes: Time limit.
            log: Receives progress lines.

        Returns:
            The job's report (``status`` still ``running`` if the wait ended first).
        """
        from . import jobs

        scope = jobs.parse_scope(plan["scope"])
        res = self.client.post("/mining/jobs", json={
            "kind": scope.kind, "name": scope.name, "source": scope.source, "paradigm": plan["paradigm"],
            "max_trials": plan["trials"], "max_minutes": minutes, "seed": plan["seed"], "workers": workers,
            "banned": [], "hint": "", "note": "nightly rotation"})
        res.raise_for_status()
        job_id = res.json()["job_id"]
        log(f"job {job_id} queued on the lab server")
        deadline = time.monotonic() + minutes * 60 + 900
        rep: dict[str, Any] = {}
        while time.monotonic() < deadline:
            rep = self.client.get(f"/mining/jobs/{job_id}").json()
            if rep.get("status") in FINAL:
                return rep
            time.sleep(self.poll)
        return {**rep, "status": rep.get("status", "running"), "message": "still running when the night's wait ended"}

    def judge(self, job_id: str, strategy: str) -> dict[str, Any]:
        """See ``_Local.judge``.

        Args:
            job_id: Job id.
            strategy: Candidate name.

        Returns:
            Its report card.
        """
        res = self.client.post(f"/mining/jobs/{job_id}/gauntlet", json={"strategy": strategy, "seed": 7}, timeout=1800)
        res.raise_for_status()
        return res.json()


def run(api: str = "http://127.0.0.1:8765/api", workers: int = 4, minutes: float = 40.0,
        log: Callable[[str], None] = print, now: datetime | None = None, notify: bool = True) -> dict[str, Any]:
    """Run tonight's mining job and judge its best only if it clears the bar.

    Args:
        api: Lab API to use when it answers.
        workers: Evaluation processes.
        minutes: Wall-clock limit of the job.
        log: Receives progress lines.
        now: Time (tz-aware); None means now.
        notify: Send the result as an info alert.

    Returns:
        ``status`` (``mined``, ``judged``, ``waiting`` or ``skipped``), ``message`` in plain words, and when a job
        ran ``plan``, ``job_id``, ``N``, ``luck``, ``bar``, ``best``, ``top`` (up to three candidates) and ``card``.
    """
    from ..paper.nightly import api_up

    now = now or now_utc()
    lab: Any = _Api(api) if api_up(api) else _Local()
    out: dict[str, Any]
    try:
        factor = lab.factor()
        if factor < 2:
            out = {"status": "waiting", "message": f"Mining waits: the thresholds count mined trials {factor:g}x; "
                                                   "calibration at mining scale needs at least 2x."}
        elif why := busy(lab.running()):
            out = {"status": "skipped", "message": f"Mining skipped tonight: {why}."}
        else:
            plan = pick(lab.budgets(), scopes(), owner_day(now))
            if "skip" in plan:
                out = {"status": "skipped", "message": f"Mining skipped: {plan['skip']}."}
            else:
                log(f"mining {plan['scope']} with {plan['paradigm']}: {plan['trials']} trials, seed {plan['seed']}")
                out = _summarise(plan, lab.mine(plan, workers, minutes, log))
                if out.get("clears"):
                    card = lab.judge(out["job_id"], out["top"][0]["strategy"])
                    out.update(status="judged", card={k: card.get(k) for k in ("card_id", "verdict", "failed_stage")})
                    out["message"] += (f" The best went to the gauntlet: card {card.get('card_id')} "
                                       f"{card.get('verdict')}" + (f" at {card['failed_stage']}"
                                                                   if card.get("failed_stage") else "")
                                       + ". A pass is research evidence, not a trading decision.")
    except (httpx.HTTPError, KeyError, ValueError) as err:
        out = {"status": "skipped", "message": f"Mining failed tonight: {type(err).__name__}: {err}"}
    if notify:
        item = alerts.Alert(rule="mining_nightly", severity="warn" if "failed" in out["message"] else "info",
                            key=f"mining:{owner_day(now).isoformat()}", title="Nightly mining",
                            message=out["message"], link="/mining")
        try:
            alerts.record([item], alerts.deliver([item]))
        except Exception:  # the result is still returned and printed
            pass
    return out


def _summarise(plan: dict[str, Any], rep: dict[str, Any]) -> dict[str, Any]:
    """The night's result from a job report.

    Args:
        plan: Output of ``pick``.
        rep: The finished job's report.

    Returns:
        ``status, message, plan, job_id, N, luck, bar, best, top, clears``.
    """
    p = rep.get("progress") or {}
    best, bar = p.get("best_edge"), p.get("bar")
    top = [{"strategy": t["strategy"], "text": t.get("text", ""), "edge": t.get("edge"), "dsr": t.get("dsr"),
            "corr": (t.get("originality") or {}).get("corr"), "rule": (t.get("originality") or {}).get("rule")}
           for t in (rep.get("top") or [])[:3]]
    clears = best is not None and bar is not None and best >= bar and bool(top)
    msg = (f"{plan['scope']} with {plan['paradigm']}: {p.get('n_trials', 0)} trials, job {rep.get('job_id')} "
           f"{rep.get('status')}. ")
    if bar is not None and best is not None:
        msg += (f"With N = {p.get('N')} luck alone reaches an edge of {p.get('sr0', float('nan')):.2f}; DSR 0.95 "
                f"needs {bar:.2f}; the best found {best:+.2f}"
                + (", which clears it." if clears else ", so nothing is judged."))
    return {"status": "mined", "message": msg, "plan": plan, "job_id": rep.get("job_id"), "N": p.get("N"),
            "luck": p.get("sr0"), "bar": bar, "best": best, "top": top, "clears": clears}
