"""Mining jobs: a budgeted search on one research question, every candidate a ledger trial, and a report to read.

A job searches one scope (an instrument or a universe) with one paradigm, within a trial budget and a time limit.
Every spec it evaluates is recorded in the ledger under the scope's mining question ``mined:<scope>:1d`` with
source ``mining``, so N rises with every try. The Deflated Sharpe of any later candidate on that scope, mined or
not, counts them (decision 0004 §3): mining on a scope raises the bar for everything else tested there.

Design:

- **Budgets per question.** Each mining question has a cap on its trials (``DEFAULT_BUDGET`` unless the owner set
  another, with a reason, in ``mining_budgets``); a job never takes more than what is left. Raising a budget is
  audited.
- **Research data only** (``evaluate``). The lockbox stays sealed; one lockbox per mining question, shared by every
  mined candidate on that scope, so it can't become a selection device.
- **The race, visible.** After each batch the report adds a point to the best-so-far curve beside the luck
  threshold at the N reached: the best edge Sharpe luck alone would produce among N trials (Deflated Sharpe, with
  the spread method of the thresholds in force) and roughly where the Deflated Sharpe reaches 0.95. Gençay (2026)
  calls it the evaporation curve.
- **What it found, read critically.** Top candidates are de-duplicated by the correlation of their daily edges,
  each with its Deflated Sharpe at the job's N, its complexity, its turnover, and its originality: the largest
  correlation of its daily edge with the lab's known rules on the same scope (their published defaults, evaluated
  first as reference trials). FactorBench (2026) found most mined predictiveness was known exposures in disguise.
- **The gauntlet decides.** ``send_to_gauntlet`` judges a candidate like any other card; nothing here is a verdict.
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd

from .. import audit, code_version, config, db, ledger, lockbox
from ..gauntlet import pipeline, stats, thresholds
from ..gauntlet.subjects import BarSubject, PanelSubject
from ..portfolio.panel import UNIVERSES
from ..timeutil import naive_utc
from . import grammar
from . import search as searching
from .evaluate import Evaluator, Pool, Result, Scope
from .grammar import Space

DEFAULT_BUDGET = 2000  # trials per mining question until the owner sets another
TOP_K = 40             # candidates kept with their daily edges while a job runs
SHOW = 10              # candidates in the report after de-duplication
SAME = 0.9             # daily-edge correlation above which two candidates are the same idea
SAVE_EVERY = 3.0       # seconds between progress saves
Z95 = 1.6448536269514722
STATUSES = ("queued", "running", "done", "stopped", "failed", "refused")

_stops: dict[str, threading.Event] = {}
_queue: queue.Queue[str] = queue.Queue()
_runner: threading.Thread | None = None
_runner_lock = threading.Lock()


def reports_dir():
    """Folder of mining job reports.

    Returns:
        ``<data>/reports/mining``.
    """
    path = config.reports_dir() / "mining"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------------------------------------------
# Budgets


def budget_of(question: str) -> dict[str, Any]:
    """A mining question's trial budget and how much of it is used.

    Args:
        question: ``mined:<scope>:<interval>``.

    Returns:
        ``question, max_trials, used, remaining, reason, updated_at``.
    """
    rows = db.rows("SELECT * FROM mining_budgets WHERE question = ?", [question])
    used = int(db.scalar("SELECT count(*) FROM trials WHERE question = ? AND source = 'mining'", [question]) or 0)
    cap = int(rows[0]["max_trials"]) if rows else DEFAULT_BUDGET
    return {"question": question, "max_trials": cap, "used": used, "remaining": max(0, cap - used),
            "reason": rows[0]["reason"] if rows else "default", "updated_at": rows[0]["updated_at"] if rows else None}


def budgets() -> list[dict[str, Any]]:
    """Every mining question with a budget or trials.

    Returns:
        ``budget_of`` rows, by question.
    """
    qs = {r["question"] for r in db.rows("SELECT question FROM mining_budgets")}
    qs |= {r["question"] for r in db.rows("SELECT DISTINCT question FROM trials WHERE source = 'mining'")}
    return [budget_of(q) for q in sorted(qs)]


def set_budget(question: str, max_trials: int, reason: str, actor: str = "user") -> dict[str, Any]:
    """Set a mining question's trial budget.

    Args:
        question: ``mined:<scope>:<interval>``.
        max_trials: New cap on its mining trials.
        reason: Why, in plain words.
        actor: Who changed it.

    Returns:
        The budget after the change.

    Raises:
        ValueError: Not a mining question, a negative cap or no reason.
    """
    if not question.startswith(f"{grammar.FAMILY}:") or question.count(":") != 2:
        raise ValueError("a mining question looks like mined:<scope>:1d")
    if max_trials < 0:
        raise ValueError("the budget can't be negative")
    if not reason.strip():
        raise ValueError("a reason is required")
    before = budget_of(question)
    with db.connect() as con:
        con.execute("INSERT OR REPLACE INTO mining_budgets VALUES (?, ?, ?, ?, ?)",
                    [question, int(max_trials), naive_utc(), reason.strip(), actor])
    audit.log("mining.budget", target=question, reason=reason, actor=actor,
              details={"from": before["max_trials"], "to": int(max_trials)})
    return budget_of(question)


# ---------------------------------------------------------------------------------------------------------------
# Jobs


def make_scope(kind: str, name: str, source: str = "", costs: str = "auto") -> Scope:
    """A scope from its parts, checked.

    Args:
        kind: ``bars`` or ``panel``.
        name: Instrument or universe.
        source: Data source of an instrument (bars).
        costs: Cost preset of an instrument.

    Returns:
        The scope.

    Raises:
        ValueError: Unknown kind, an unknown universe, or an instrument without a source.
    """
    if kind == "panel":
        if name not in UNIVERSES:
            raise ValueError(f"unknown universe {name!r}; use one of {', '.join(UNIVERSES)}")
        return Scope("panel", name)
    if kind == "bars":
        if not source:
            raise ValueError("an instrument needs its data source (yahoo or binance)")
        return Scope("bars", name, source, "1d", costs)
    raise ValueError("scope kind must be bars or panel")


def parse_scope(text: str, source: str = "", costs: str = "auto") -> Scope:
    """A scope from what a person types: a universe name, or an instrument (source guessed when not given).

    Args:
        text: ``crypto_top30``, ``SPY``, ``BTCUSDT``...
        source: Data source of an instrument; empty guesses ``binance`` for ``...USDT`` and ``yahoo`` otherwise.
        costs: Cost preset of an instrument.

    Returns:
        The scope.
    """
    if text in UNIVERSES:
        return make_scope("panel", text)
    return make_scope("bars", text, source or ("binance" if text.endswith("USDT") else "yahoo"), costs)


def create(scope: Scope, paradigm: str, max_trials: int, max_minutes: float = 30.0, seed: int = 1,
           workers: int = 1, banned: list[str] | None = None, hint: str = "", note: str = "",
           actor: str = "user") -> dict[str, Any]:
    """Register a job (status ``queued``); ``run`` or ``submit`` starts it.

    Args:
        scope: What to search.
        paradigm: One of ``search.PARADIGMS``.
        max_trials: Trials this job may evaluate (at most what is left of the question's budget).
        max_minutes: Wall-clock limit.
        seed: Random seed.
        workers: Processes evaluating specs.
        banned: Operators, terminals, rules or components to leave out.
        hint: Research direction for the ``llm`` paradigm.
        note: Why this job, in plain words.
        actor: Who started it.

    Returns:
        The job row.

    Raises:
        ValueError: Unknown paradigm, a non-positive budget or time, or unknown banned names.
    """
    if paradigm not in searching.PARADIGMS:
        raise ValueError(f"paradigm must be one of {', '.join(searching.PARADIGMS)}")
    if max_trials < 1 or max_minutes <= 0:
        raise ValueError("the trial budget and the time limit must be positive")
    known = set(grammar.COMPONENTS) | set(grammar.BAR_RULES) | set(grammar.STATIC_RULES) | set(
        grammar.DYNAMIC_RULES) | set(grammar.formula.OPS) | set(grammar.formula.TERMINALS)
    unknown = sorted(set(banned or []) - known)
    if unknown:
        raise ValueError(f"unknown names to ban: {', '.join(unknown)}")
    job_id = uuid.uuid4().hex[:10]
    settings = {"scope": scope.to_dict(), "max_trials": int(max_trials), "max_minutes": float(max_minutes),
                "seed": int(seed), "workers": int(max(1, workers)), "banned": sorted(set(banned or [])),
                "hint": hint, "note": note}
    with db.connect() as con:
        con.execute("""INSERT INTO mining_jobs (job_id, created_at, question, scope, paradigm, status, settings,
                       n_trials, actor) VALUES (?, ?, ?, ?, ?, 'queued', ?, 0, ?)""",
                    [job_id, naive_utc(), scope.question, scope.name, paradigm, db.dumps(settings), actor])
    audit.log("mining.create", target=job_id, actor=actor, reason=note,
              details={"question": scope.question, "paradigm": paradigm, "max_trials": max_trials})
    _save(job_id, {"job_id": job_id, "status": "queued", "question": scope.question, "scope": scope.to_dict(),
                   "paradigm": paradigm, "settings": settings, "created_at": naive_utc().isoformat(timespec="seconds"),
                   "progress": {"n_trials": 0}, "curve": [], "top": [], "cards": []})
    return job(job_id)


def job(job_id: str) -> dict[str, Any]:
    """One job's row.

    Args:
        job_id: Job id.

    Returns:
        The row with ``settings`` decoded.

    Raises:
        KeyError: Unknown job.
    """
    rows = db.rows("SELECT * FROM mining_jobs WHERE job_id = ?", [job_id])
    if not rows:
        raise KeyError(f"no mining job {job_id}")
    r = rows[0]
    r["settings"] = json.loads(r["settings"] or "{}")
    return r


def jobs(limit: int = 100) -> list[dict[str, Any]]:
    """Recent jobs, newest first.

    Args:
        limit: Most rows.

    Returns:
        Job rows with ``settings`` decoded and ``live`` (whether this process is running it).
    """
    rows = db.rows("SELECT * FROM mining_jobs ORDER BY created_at DESC LIMIT ?", [limit])
    for r in rows:
        r["settings"] = json.loads(r["settings"] or "{}")
        r["live"] = r["job_id"] in _stops
    return rows


def load(job_id: str) -> dict[str, Any]:
    """A job's report.

    Args:
        job_id: Job id.

    Returns:
        The report (see ``run``).

    Raises:
        FileNotFoundError: Unknown job.
    """
    path = reports_dir() / f"{job_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no mining job {job_id}")
    return json.loads(path.read_text())


def _save(job_id: str, report: dict[str, Any]) -> None:
    """Write a job's report atomically.

    Args:
        job_id: Job id.
        report: The report.
    """
    path = reports_dir() / f"{job_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(db.json_safe(report), default=str))
    tmp.replace(path)


def _update(job_id: str, **fields: Any) -> None:
    """Update a job's row.

    Args:
        job_id: Job id.
        **fields: Columns and values.
    """
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db.connect() as con:
        con.execute(f"UPDATE mining_jobs SET {sets} WHERE job_id = ?", [*fields.values(), job_id])


def stop(job_id: str) -> bool:
    """Ask a running or queued job to stop after its current batch.

    Args:
        job_id: Job id.

    Returns:
        True when the job was running or queued in this process.
    """
    ev = _stops.get(job_id)
    if ev is None:
        row = job(job_id)
        if row["status"] == "queued":
            _update(job_id, status="stopped", finished_at=naive_utc(), message="stopped before it started")
            rep = load(job_id)
            rep["status"] = "stopped"
            _save(job_id, rep)
            return True
        return False
    ev.set()
    return True


def submit(job_id: str, log: Callable[[str], None] | None = None) -> None:
    """Queue a job on the single background runner (one job at a time, so one DuckDB writer).

    Args:
        job_id: A ``queued`` job.
        log: Progress callback for the runner.
    """
    global _runner
    _stops[job_id] = threading.Event()
    _queue.put(job_id)
    with _runner_lock:
        if _runner is None or not _runner.is_alive():
            _runner = threading.Thread(target=_drain, args=(log or (lambda _m: None),), daemon=True)
            _runner.start()


def _drain(log: Callable[[str], None]) -> None:
    """Background runner: run queued jobs one after another.

    Args:
        log: Progress callback.
    """
    while True:
        try:
            job_id = _queue.get(timeout=5)
        except queue.Empty:
            return
        try:
            if _stops.get(job_id) is not None and _stops[job_id].is_set():
                stop(job_id)
                continue
            run(job_id, log=log)
        except Exception as err:  # recorded on the job; the runner keeps going
            _update(job_id, status="failed", finished_at=naive_utc(), message=str(err)[:500])
        finally:
            _stops.pop(job_id, None)


class _Tracker:
    """Everything a job accumulates while its search runs.

    Attributes:
        prior: Annual edge Sharpes of the trials on the scope before the job.
        edges: Annual edge Sharpes of the job's scored trials.
        top: Best results with their daily edges.
        coverage: Counters for the coverage map.
        curve: Best-so-far points with the luck threshold at each N.
    """

    def __init__(self, prior: list[float], n_days: int, dpy: int, method: str,
                 prior_len: list[float] | None = None) -> None:
        """Start tracking.

        Args:
            prior: Edge Sharpes already on the scope.
            n_days: Research days (for the luck variance).
            dpy: Days per year.
            method: Spread method of the thresholds in force (``dsr_variance``).
            prior_len: Each prior trial's calendar span as a multiple of the job's, aligned with ``prior``; None
                means the same span. A shorter trial spreads wider by luck (decision 0004 addendum).
        """
        keep = [i for i, x in enumerate(prior) if x is not None and math.isfinite(x)]
        self.prior = [prior[i] for i in keep]
        self.prior_len = [prior_len[i] for i in keep] if prior_len is not None else [1.0] * len(keep)
        self.edges: list[float] = []
        self.gross: list[float] = []
        self.top: list[Result] = []
        self.n_days, self.dpy, self.method = n_days, dpy, method
        self.luck_ann = stats.sr_variance(0.0, n_days) * dpy  # one Sharpe's sampling variance with no edge, annual
        self.coverage = {k: Counter() for k in ("signals", "filters", "exits", "sessions", "sizing", "ops",
                                                "terminals", "windows")}
        self.best_by: dict[str, dict[str, float]] = defaultdict(dict)
        self.cells: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.curve: list[dict[str, Any]] = []
        self.n = self.valid = self.leaks = 0

    def threshold(self) -> dict[str, float]:
        """The luck threshold at the current N.

        Returns:
            ``N``, ``sr0`` (annual edge Sharpe luck alone reaches) and ``bar`` (about where the Deflated Sharpe
            reaches 0.95).
        """
        x = np.asarray(self.prior + self.edges, dtype=float)
        n = len(x)
        v = stats.trial_variance(x, self.method, self.luck_ann) if n > 1 else 0.0
        sr0 = stats.expected_max_sharpe(n, v)
        return {"N": n, "sr0": sr0, "bar": sr0 + Z95 * math.sqrt(self.luck_ann)}

    def add(self, results: list[Result]) -> None:
        """Count a batch.

        Args:
            results: The batch's results.
        """
        for r in results:
            self.n += 1
            self.leaks += r.leak
            sig = grammar.signature(r.spec)
            label = sig["signal"] if sig["signal"] != "formula" else f"formula: {_root(r.spec)}"
            for k, v in (("signals", label), ("filters", sig["filter"]), ("exits", sig["exit"]),
                         ("sessions", sig["session"]), ("sizing", sig["sizing"])):
                self.coverage[k][v] += 1
            self.coverage["ops"].update(sig["ops"])
            self.coverage["terminals"].update(sig["terminals"])
            self.coverage["windows"].update(str(w) for w in sig["windows"])
            if not r.ok:
                continue
            self.valid += 1
            self.edges.append(r.edge)
            self.gross.append(r.gross_edge)
            for k, v in (("signals", label), ("filters", sig["filter"]), ("exits", sig["exit"]),
                         ("sessions", sig["session"]), ("sizing", sig["sizing"])):
                self.best_by[k][v] = max(self.best_by[k].get(v, -math.inf), r.edge)
            for comp in (f"filter: {sig['filter']}", f"exit: {sig['exit']}", f"session: {sig['session']}",
                         f"sizing: {sig['sizing']}"):
                self.cells[(label, comp)].append(r.edge)
            if r.daily_edge is not None and (len(self.top) < TOP_K or r.edge > self.top[-1].edge):
                self.top.append(r)
                self.top.sort(key=lambda t: -t.edge)
                del self.top[TOP_K:]
        th = self.threshold()
        self.curve.append({"n": self.n, "N": th["N"], "best": self.top[0].edge if self.top else None,
                           "sr0": th["sr0"], "bar": th["bar"]})


def _days(trial: dict[str, Any]) -> float:
    """Calendar days a ledger trial covers.

    Args:
        trial: A ``ledger.selection_set`` row (``start`` and ``end``).

    Returns:
        Days, first and last included; NaN when a date is missing (``deflate`` then treats it as the job's span).
    """
    if not trial.get("start") or not trial.get("end"):
        return float("nan")
    return float((pd.Timestamp(trial["end"]) - pd.Timestamp(trial["start"])).days + 1)


def _root(spec: dict[str, Any]) -> str:
    """The family of a formula's root operator, for grouping in the coverage map.

    Args:
        spec: A formula spec.

    Returns:
        ``ts``, ``cs``, ``unary``, ``binary`` or ``ts2``.
    """
    return grammar.formula.kind_of(spec["signal"]["formula"]["op"])


def reference_specs(space: Space) -> list[dict[str, Any]]:
    """The lab's known rules at their published defaults, as specs: the baseline every job evaluates first.

    Args:
        space: The space.

    Returns:
        Normalised specs, one per allowed rule.
    """
    out = []
    for name in space.rules:
        if space.allowed(name) and space.allowed("rule"):
            out.append(grammar.normalize({"signal": {"rule": name, "params": {}}}, space))
    return out


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    """Correlation of two daily series, ignoring missing days.

    Args:
        a: First series.
        b: Second series.

    Returns:
        The correlation, or NaN.
    """
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 30 or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
        return float("nan")
    return float(np.corrcoef(a[ok], b[ok])[0, 1])


def run(job_id: str, log: Callable[[str], None] = print, proposer: Any = None) -> dict[str, Any]:
    """Run a queued job to the end of its budget, its time limit or a stop request.

    Args:
        job_id: The job.
        log: Progress callback.
        proposer: For the ``llm`` paradigm, a ``llm.Proposer``; None builds one with the default provider.

    Returns:
        The final report: ``job_id, status, question, scope, paradigm, settings, research, n_days, dpy, space,
        thresholds_version, dsr_variance, prior_n, progress, curve, reference, top, coverage, histogram, cells,
        proposals, outside, stopped, message, created_at, started_at, finished_at, code, cards``.

    Raises:
        KeyError: Unknown job.
    """
    row = job(job_id)
    st = row["settings"]
    scope = Scope(**st["scope"])
    stop_ev = _stops.setdefault(job_id, threading.Event())
    rep = load(job_id)
    rep.update(status="running", started_at=naive_utc().isoformat(timespec="seconds"), code=code_version())
    _update(job_id, status="running", started_at=naive_utc())
    pool = None
    try:
        ev = Evaluator.for_scope(scope, tuple(st["banned"]))
        space = ev.space
        budget = budget_of(scope.question)
        n_max = min(int(st["max_trials"]), budget["remaining"])
        version, th = thresholds.current()
        method = str(th.get("dsr_variance", thresholds.LEGACY["dsr_variance"]))
        prior = ledger.selection_set(scope.name, scope.interval)
        rep.update(research=list(ev.research), n_days=ev.n_days, dpy=ev.dpy, space=space.to_dict(),
                   thresholds_version=version, dsr_variance=method, prior_n=len(prior), budget=budget)
        if n_max <= 0:
            msg = (f"the question's budget is used ({budget['used']} of {budget['max_trials']} trials); raise it "
                   "with a reason to mine more")
            rep.update(status="refused", message=msg, finished_at=naive_utc().isoformat(timespec="seconds"))
            _save(job_id, rep)
            _update(job_id, status="refused", message=msg, finished_at=naive_utc())
            log(msg)
            return rep
        span = (pd.Timestamp(ev.research[1]) - pd.Timestamp(ev.research[0])).days + 1
        track = _Tracker([t["edge_sharpe"] for t in prior], ev.n_days, ev.dpy, method,
                         [_days(t) / span for t in prior])
        last_save = [time.monotonic()]
        live: dict[str, Any] = {"search": None}

        def record(results: list[Result]) -> None:
            ledger.record_trials([
                {"question": scope.question, "family": grammar.FAMILY, "strategy": r.name, "params": r.params,
                 "symbol": scope.name, "start": ev.research[0], "end": ev.research[1], "source": "mining",
                 "metrics": {**r.metrics, "job": job_id}, "data_hash": ev.data_hash} for r in results])
            grammar.save_specs([(r.spec, space) for r in results], job_id)
            track.add(results)
            if time.monotonic() - last_save[0] >= SAVE_EVERY:
                last_save[0] = time.monotonic()
                _progress(rep, track, live["search"])
                _save(job_id, rep)
                _update(job_id, n_trials=track.n, best_strategy=track.top[0].name if track.top else None,
                        best_edge=track.top[0].edge if track.top else None)
                log(f"{job_id}: {track.n} trials, best edge {track.top[0].edge:+.2f}" if track.top
                    else f"{job_id}: {track.n} trials")

        pool = Pool(ev, int(st["workers"]))
        refs = reference_specs(space) if n_max > 2 * len(reference_specs(space)) else []
        ref_results = pool.evaluate(refs, seed=int(st["seed"])) if refs else []
        if ref_results:
            record(ref_results)
        rep["reference"] = [{"rule": r.spec["signal"]["rule"], "strategy": r.name, "params": r.params,
                             "edge": r.edge if r.ok else None, "gross_edge": r.gross_edge,
                             "turnover": r.metrics.get("turnover")} for r in ref_results]
        prop = proposer
        if row["paradigm"] == "llm" and prop is None:
            from .llm import Proposer, describe_scope

            prop = Proposer(describe_scope(scope, space), hint=st.get("hint", ""))
        srch = searching.Search(space, lambda specs: pool.evaluate(specs, seed=int(st["seed"]) + track.n),
                                searching.Budget(n_max - len(ref_results), float(st["max_minutes"]) * 60.0),
                                on_batch=record, should_stop=stop_ev.is_set)
        live["search"] = srch
        log(f"{job_id}: {row['paradigm']} on {scope.question}, up to {n_max} trials "
            f"({len(prior)} trials already on {scope.name})")
        outcome = searching.run(row["paradigm"], srch, int(st["seed"]), batch=max(16, 2 * int(st["workers"])),
                                proposer=prop)
        status = "stopped" if outcome.stopped == "stopped" else "done"
        _progress(rep, track, srch)
        _finish(rep, track, ref_results, ev)
        if prop is not None and hasattr(prop, "accepted"):
            rep["proposals"] = [{k: a[k] for k in ("strategy", "params", "text", "rationale")} for a in prop.accepted]
            rep["outside"] = prop.outside
            rep["llm"] = {"calls": prop.calls, "rejected": prop.rejected, "model": prop.llm.model}
            for t in rep["top"]:
                t["rationale"] = prop.rationale_of(t["spec"])
        rep.update(status=status, stopped=outcome.stopped, finished_at=naive_utc().isoformat(timespec="seconds"),
                   message=f"{track.n} trials ({track.valid} scored); ended by {outcome.stopped}")
        _save(job_id, rep)
        _update(job_id, status=status, n_trials=track.n, best_strategy=track.top[0].name if track.top else None,
                best_edge=track.top[0].edge if track.top else None, finished_at=naive_utc(),
                message=rep["message"], report_path=str(reports_dir() / f"{job_id}.json"))
        audit.log("mining.run", target=job_id, actor=row["actor"] or "user",
                  details={"question": scope.question, "paradigm": row["paradigm"], "trials": track.n,
                           "best": rep["top"][0]["edge"] if rep["top"] else None, "status": status})
        log(f"{job_id}: {rep['message']}")
        return rep
    except Exception as err:
        rep.update(status="failed", message=str(err)[:500], finished_at=naive_utc().isoformat(timespec="seconds"))
        _save(job_id, rep)
        _update(job_id, status="failed", message=str(err)[:500], finished_at=naive_utc())
        raise
    finally:
        if pool is not None:
            pool.close()
        _stops.pop(job_id, None)


def _progress(rep: dict[str, Any], track: _Tracker, srch: Any) -> None:
    """Copy the running totals into a report.

    Args:
        rep: The report.
        track: The tracker.
        srch: The search (for elapsed time), or None.
    """
    best = track.top[0] if track.top else None
    rep["progress"] = {"n_trials": track.n, "n_valid": track.valid, "leaks": track.leaks,
                       "elapsed": round(srch.outcome.elapsed, 1) if srch is not None else 0.0,
                       "best_edge": best.edge if best else None, "best_strategy": best.name if best else None,
                       "best_text": grammar.describe(best.spec) if best else None, **track.threshold()}
    rep["curve"] = _thin(track.curve, 400)


def _thin(points: list[dict[str, Any]], most: int) -> list[dict[str, Any]]:
    """At most ``most`` points of a curve, the last one always kept.

    Args:
        points: Curve points.
        most: Cap.

    Returns:
        The thinned curve.
    """
    if len(points) <= most:
        return points
    step = int(math.ceil(len(points) / most))
    return points[::step] + ([points[-1]] if (len(points) - 1) % step else [])


def _finish(rep: dict[str, Any], track: _Tracker, refs: list[Result], ev: Evaluator) -> None:
    """Add the top candidates, coverage and the edge histogram to a report.

    Args:
        rep: The report.
        track: The tracker.
        refs: Reference results (the known rules).
        ev: The evaluator.
    """
    trial_sr = np.asarray(track.prior + track.edges, dtype=float)
    length = np.asarray(track.prior_len + [1.0] * len(track.edges), dtype=float)
    ref_daily = [(r.spec["signal"]["rule"], r.daily_edge) for r in refs if r.ok and r.daily_edge is not None]
    kept: list[Result] = []
    for r in track.top:
        if all(not (_corr(r.daily_edge, k.daily_edge) > SAME) for k in kept):
            kept.append(r)
        if len(kept) >= SHOW:
            break
    top = []
    for r in kept:
        d = pipeline.deflate(r.daily_edge.astype(float), trial_sr, ev.dpy, track.method, length)
        corrs = [(name, _corr(r.daily_edge, e)) for name, e in ref_daily]
        corrs = [(n, c) for n, c in corrs if math.isfinite(c)]
        close = max(corrs, key=lambda x: abs(x[1])) if corrs else (None, float("nan"))
        node = r.spec["signal"].get("formula")
        top.append({
            "strategy": r.name, "params": r.params, "spec": r.spec, "text": grammar.describe(r.spec),
            "formula": grammar.formula.to_text(node) if node else None, "edge": r.edge, "gross_edge": r.gross_edge,
            "sharpe": r.metrics.get("sharpe"), "cagr": r.metrics.get("cagr"), "max_dd": r.metrics.get("max_dd"),
            "turnover": r.metrics.get("turnover"), "exposure": r.metrics.get("exposure"),
            "nodes": r.metrics.get("nodes"), "dsr": d["dsr"], "sr0": d["sr0"] * math.sqrt(ev.dpy),
            "n_trials": d["n_trials"], "originality": {"rule": close[0], "corr": close[1]},
            "session": r.spec.get("session", "all"),
        })
    rep["top"] = top
    edges = np.asarray(track.edges, dtype=float)
    bins = np.round(np.arange(-3.0, 3.01, 0.1), 2)
    counts, _ = np.histogram(np.clip(edges, -2.999, 2.999), bins=bins)
    rep["histogram"] = {"bins": bins.tolist(), "counts": counts.tolist(), "luck_sd": math.sqrt(track.luck_ann),
                        "n": int(len(edges)), "below_zero": int((edges < 0).sum())}
    rep["coverage"] = {k: dict(c.most_common()) for k, c in track.coverage.items()}
    rep["best_by"] = {k: dict(v) for k, v in track.best_by.items()}
    rows = sorted({a for a, _ in track.cells})
    cols = sorted({b for _, b in track.cells})
    rep["cells"] = {"rows": rows, "cols": cols,
                    "values": [[len(track.cells.get((a, b), [])), max(track.cells[(a, b)])
                                if track.cells.get((a, b)) else None] for a in rows for b in cols]}


# ---------------------------------------------------------------------------------------------------------------
# Sending a candidate to the gauntlet


def send_to_gauntlet(job_id: str, strategy: str, seed: int = 7, actor: str = "user") -> dict[str, Any]:
    """Judge one of a job's candidates with the gauntlet, like any other card.

    The card's question is the mining question, so it shares one lockbox with every mined candidate on the scope.
    Its Deflated Sharpe counts every trial on the scope recorded before the run, this job's included.

    Args:
        job_id: The job.
        strategy: A ``mined_...`` name from the job's top candidates.
        seed: Seed of the gauntlet run.
        actor: Who sent it.

    Returns:
        The report card.

    Raises:
        FileNotFoundError: Unknown job.
        KeyError: The strategy isn't among the job's top candidates.
        ValueError: Too little research data (from the gauntlet).
    """
    rep = load(job_id)
    cand = next((t for t in rep.get("top", []) if t["strategy"] == strategy), None)
    if cand is None:
        raise KeyError(f"{strategy} isn't among job {job_id}'s candidates")
    scope = Scope(**rep["scope"])
    space = Space.from_dict(rep["space"])
    strat = grammar.compile_spec(cand["spec"], space)
    sealed = lockbox.sealed_from()
    if scope.kind == "bars":
        subject = BarSubject.load(scope.source, scope.name, scope.interval, strat, sealed, scope.costs)
    else:
        subject = PanelSubject.load(scope.name, strat, sealed)
    subject.meta["mined"] = {"job": job_id, "paradigm": rep["paradigm"], "text": cand["text"],
                             "formula": cand.get("formula"), "spec": cand["spec"], "session": cand.get("session")}
    card = pipeline.judge_subject(subject, cand["params"], seed, actor)
    rep.setdefault("cards", []).append({"strategy": strategy, "card_id": card["card_id"], "verdict": card["verdict"],
                                        "failed_stage": card["failed_stage"],
                                        "at": naive_utc().isoformat(timespec="seconds")})
    _save(job_id, rep)
    audit.log("mining.gauntlet", target=card["card_id"], actor=actor,
              details={"job": job_id, "strategy": strategy, "verdict": card["verdict"]})
    return card


def frame(job_id: str) -> pd.DataFrame:
    """A job's trials from the ledger, for analysis.

    Args:
        job_id: The job.

    Returns:
        One row per trial: ``strategy, params, edge_sharpe, sharpe, created_at``.
    """
    rows = db.rows("""SELECT strategy, params, edge_sharpe, sharpe, created_at FROM trials
                      WHERE source = 'mining' AND json_extract_string(metrics, '$.job') = ?
                      ORDER BY created_at""", [job_id])
    return pd.DataFrame(rows)
