"""Calibration at mining scale: a "miner" researcher searches synthetic markets and submits its best to the gauntlet.

The exit criterion of M5: noise calibration must still hold when candidates come from a search of hundreds or
thousands of trials rather than a grid of at most 300 settings. Adaptive search is the hard case for the Deflated
Sharpe. Its trials concentrate where earlier ones scored well, so the best of N adaptive trials can sit further in
the tail than the best of N independent ones, while their spread (which sets the luck threshold) shrinks. Only the
measurement says whether the threshold still holds.

Each run:

1. builds a synthetic market (the same generators as studies A-G): GARCH single-instrument bars (20 years), an
   eight-asset panel (18 years) or a top-30 crypto cross-section (5.6 years), as noise or with a planted edge;
2. runs one paradigm (random, tpe, gp) over the full grammar for a fixed budget of trials;
3. submits the in-sample best to the unchanged gauntlet (``pipeline.evaluate`` through ``calibration._finish``),
   deflated against every trial the search made, as ``jobs.send_to_gauntlet`` does on a fresh scope.

Families are ``mine/<paradigm>/<market>``. Noise must pass at most 5% of the time in every family. Power on planted
markets is reported at the true edge of the market type's default rule (trend for bars and panels, momentum for the
cross-section), as in the earlier studies; the miner may find another rule that earns more from the same plant.
Every trial (the miners' and the gauntlet grids) is recorded under ``calib:`` questions.
"""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd

from .. import audit, code_version, config, ledger
from ..backtest import metrics
from ..backtest.engine import COST_PRESETS
from ..gauntlet import calibration as cal
from ..gauntlet import stats, thresholds
from ..gauntlet.pipeline import clean, deflate
from ..gauntlet.subjects import BarSubject, PanelSubject
from ..portfolio.synthetic import synthetic_panel
from ..timeutil import naive_utc
from . import grammar
from . import search as searching
from .evaluate import Evaluator

MARKETS = {"bars": 20.0, "panel": float(cal.PORTFOLIO_YEARS), "xs30": cal.XS_YEARS}  # market type -> years
PARADIGMS = ("random", "tpe", "gp")
BUDGET = {"bars": 1000, "panel": 1000, "xs30": 500}
NOISE_REPS = {"bars": 60, "panel": 40, "xs30": 30}
POWER_REPS = {"bars": 20, "panel": 15, "xs30": 15}
POWER_FAMILY = {"bars": "trend_sma", "panel": "portfolio/tsmom", "xs30": "xs30/xs_weekly_momentum"}
KEEP = ("sharpe", "cagr", "max_dd", "n_obs", "edge_sharpe")
TRUTH_YEARS = {"bars": 400.0, "panel": 150.0, "xs30": cal.XS_TRUTH_YEARS}  # long noise markets for true edges
TRUTH_SEEDS = 4
TRUTH_BASE = 888_000


def market(kind: str, years: float, trend: float, seed: int) -> dict[str, Any]:
    """A synthetic market of one type.

    Args:
        kind: ``bars``, ``panel`` or ``xs30``.
        years: Length.
        trend: Planted edge (a hidden trend; momentum in the cross-section); 0 for noise.
        seed: Random seed.

    Returns:
        ``{"bars": ...}`` or ``{"panel": ...}``, the arguments of ``Evaluator.synthetic``.
    """
    if kind == "bars":
        return {"bars": cal.synthetic_bars(years, trend, seed)}
    if kind == "panel":
        return {"panel": synthetic_panel(years, cal.PORTFOLIO_ASSETS, trend, seed)}
    return {"panel": cal.xs_panel(POWER_FAMILY["xs30"], years, trend, seed)}


def subject_of(kind: str, data: dict[str, Any], spec: dict[str, Any], space: grammar.Space):
    """The gauntlet subject of a mined spec on a synthetic market, with no lockbox.

    Args:
        kind: Market type.
        data: The market (``market``).
        spec: The mined spec.
        space: Its space.

    Returns:
        A ``BarSubject`` or ``PanelSubject``.
    """
    strat = grammar.compile_spec(spec, space)
    if "bars" in data:
        bars = data["bars"]
        return BarSubject(bars, strat, bars["ts"].iloc[-1] + pd.Timedelta(days=1), source="synthetic",
                          symbol="SYNTH", interval="1d", model=COST_PRESETS["etf"], rf=None, dpy=cal.DPY)
    panel = data["panel"]
    return PanelSubject(panel, strat, panel.ts[-1] + pd.Timedelta(days=1),
                        "synthetic_crypto30" if kind == "xs30" else "synthetic")


def mine_one(task: dict[str, Any]) -> dict[str, Any]:
    """One miner researcher: search a synthetic market, then judge the in-sample best. Runs in a worker process.

    Args:
        task: ``scenario`` (``noise_mined`` or ``power_mined``), ``family`` (``mine/<paradigm>/<market>``),
            ``market``, ``paradigm``, ``years``, ``trend``, ``seed``, ``budget``, ``th`` and, for power,
            ``target`` and ``true_edge``.

    Returns:
        The output of ``calibration._finish`` plus ``mined`` (what the search found: its best edge, the luck
        threshold it faced, the best spec in words and what it is made of) and ``miner_records`` (every trial of
        the search, compact, for the ledger).
    """
    t0 = time.perf_counter()
    data = market(task["market"], task["years"], task["trend"], task["seed"])
    ev = Evaluator.synthetic(**data, leak_cuts=0)
    srch = searching.Search(ev.space, lambda specs: [ev.evaluate(s, keep_daily=False) for s in specs],
                            searching.Budget(int(task["budget"])))
    results: list[Any] = []
    srch.on_batch = results.extend
    out = searching.run(task["paradigm"], srch, int(task["seed"]) % (2**31), batch=16)
    t_search = time.perf_counter() - t0
    records = [{"strategy": r.name, "params": r.params, "start": ev.research[0], "end": ev.research[1],
                "metrics": {k: r.metrics.get(k) for k in KEEP}} for r in results]
    valid = [r for r in results if r.ok]
    if out.best is None or not valid:  # nothing to submit: counted as a run that admitted nothing
        blank = dict.fromkeys(("edge", "net_cagr", "wf_oos", "wf_ratio", "plateau", "perm_p", "dsr", "pbo",
                               "regime_share", "regime_edge_share", "div_corr", "div_gain"), math.nan)
        return {**{k: v for k, v in task.items() if k != "th"}, "values": {**blank, "leak": 0.0}, "passed": {},
                "failed_stage": "none",
                "admitted": False, "dsr_by": {}, "sr0_by": {}, "records": [], "miner_records": records,
                "mined": {"n_trials": out.n_trials, "n_valid": 0}}
    best = out.best
    subject = subject_of(task["market"], data, best.spec, ev.space)
    k = float(task["th"].get("mining_n_factor", thresholds.LEGACY["mining_n_factor"]))
    res = cal._finish(task, subject, best.params, [r.edge for r in valid], [r.gross_edge for r in valid], True, k)
    sim = subject.simulate(best.params)
    cand_e, _ = metrics.edge(sim.net - subject.rf, sim.bench - subject.rf)
    method = str(task["th"].get("dsr_variance", thresholds.LEGACY["dsr_variance"]))
    d = deflate(cand_e, np.asarray([r.edge for r in valid]), subject.dpy, method,
                weight=np.full(len(valid), k) if k != 1.0 else None)
    # Enough to recompute the Deflated Sharpe at another N without re-running (``rejudge`` with ``n_factor``).
    res["dsr_inputs"] = {k: d[k] for k in ("sr", "n", "skew", "kurt", "rho", "var_annual", "n_trials")}
    sig = grammar.signature(best.spec)
    res["mined"] = {"n_trials": out.n_trials, "n_valid": len(valid), "edge": best.edge, "gross_edge": best.gross_edge,
                    "spec": best.spec, "text": grammar.describe(best.spec), "signal": sig["signal"],
                    "filter": sig["filter"], "exit": sig["exit"], "session": sig["session"], "sizing": sig["sizing"],
                    "nodes": sig["nodes"],
                    "sr0": res["sr0_by"].get(task["th"].get("dsr_variance", "sample")),
                    "search_s": round(t_search, 1), "total_s": round(time.perf_counter() - t0, 1)}
    res["miner_records"] = records
    return res


def _truth_one(args: tuple[str, dict[str, Any], int]) -> dict[str, Any]:
    """Edge of one mined spec on one long noise market (a pool worker).

    Args:
        args: ``(market type, spec, seed)``.

    Returns:
        ``edge`` (annual edge Sharpe after costs) and ``record`` (a compact trial for the ledger).
    """
    kind, spec, seed = args
    data = market(kind, TRUTH_YEARS[kind], 0.0, seed)
    ev = Evaluator.synthetic(**data, leak_cuts=0)
    r = ev.evaluate(grammar.normalize(spec, ev.space), keep_daily=False)
    return {"edge": r.edge if r.ok else float("nan"),
            "record": {"strategy": r.name, "params": r.params, "start": ev.research[0], "end": ev.research[1],
                       "metrics": {k: r.metrics.get(k) for k in KEEP}}}


def admitted_noise(report: dict[str, Any], pool: ProcessPoolExecutor, log: Callable[[str], None] = print,
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """How much edge the noise runs that passed really have: each winner on long fresh noise markets.

    A noise market is meant to give every rule zero edge. If a winner earns a clear edge on markets ten times
    longer, the synthetic market had something to find (decision 0005: volatility timing earns a real edge when
    volatility clusters and the drift is positive) and the pass is a true positive of that world, not luck. A
    winner whose spec wasn't saved (reports before the spec was kept) is found again by repeating its search,
    which is deterministic; the repeat must reach the same edge.

    Args:
        report: A mining calibration report with ``runs``.
        pool: Process pool.
        log: Progress callback.

    Returns:
        ``(rows, records)``: one row per admitted noise run (``family, seed, edge, true_edge, true_edges,
        reproduced, text``) and the trial records of the long-market backtests.

    Raises:
        RuntimeError: A repeated search didn't reach the recorded edge.
    """
    runs = [r for r in report["runs"] if r["scenario"] == "noise_mined" and r["admitted"]]
    todo = [r for r in runs if not r["mined"].get("spec")]
    if todo:
        log(f"repeating {len(todo)} searches to recover their winners")
        tasks = [{k: r[k] for k in ("scenario", "family", "market", "paradigm", "years", "trend", "seed")}
                 | {"budget": report["settings"]["budget"][r["market"]], "th": report["thresholds"]} for r in todo]
        for r, again in zip(todo, pool.map(mine_one, tasks), strict=True):
            if abs(again["mined"]["edge"] - r["mined"]["edge"]) > 1e-9:
                raise RuntimeError(f"{r['family']} seed {r['seed']} didn't repeat: {again['mined']['edge']} vs "
                                   f"{r['mined']['edge']}")
            r["mined"]["spec"] = again["mined"]["spec"]
            r["mined"]["reproduced"] = True
    jobs = [(r["market"], r["mined"]["spec"], TRUTH_BASE + k) for r in runs for k in range(1, TRUTH_SEEDS + 1)]
    got = list(pool.map(_truth_one, jobs))
    rows, records = [], []
    for i, r in enumerate(runs):
        part = got[i * TRUTH_SEEDS:(i + 1) * TRUTH_SEEDS]
        edges = [g["edge"] for g in part]
        records += [g["record"] for g in part]
        rows.append({"family": r["family"], "seed": r["seed"], "edge": r["mined"]["edge"],
                     "true_edge": float(np.nanmean(edges)), "true_edges": edges,
                     "reproduced": bool(r["mined"].get("reproduced")), "text": r["mined"]["text"],
                     "sizing": r["mined"]["sizing"], "filter": r["mined"]["filter"]})
    return rows, records


def tasks_for(th: dict[str, Any], base_seed: int, paradigms: tuple[str, ...], budget: dict[str, int],
              noise_reps: dict[str, int], power_reps: dict[str, int], sizes: dict[str, dict[float, float]],
              truth: dict[tuple[str, float], float]) -> list[dict[str, Any]]:
    """Every run of the study, each on its own market seed.

    Paradigms share markets: run k of a market type uses the same seed for every paradigm, so the paradigms are
    compared on identical markets with identical budgets.

    Args:
        th: Thresholds.
        base_seed: Market seeds start at ``base_seed + 1``.
        paradigms: Paradigms to compare.
        budget: Trials per search, by market type.
        noise_reps: Noise markets per market type.
        power_reps: Planted markets per market type and edge.
        sizes: Planted sizes by market type and target edge.
        truth: True edges by ``(market type, target edge)``.

    Returns:
        Tasks for ``mine_one``.
    """
    tasks = []
    seed = base_seed
    for kind, years in MARKETS.items():
        for _ in range(noise_reps.get(kind, 0)):
            seed += 1
            for p in paradigms:
                tasks.append({"scenario": "noise_mined", "family": f"mine/{p}/{kind}", "market": kind, "paradigm": p,
                              "years": years, "trend": 0.0, "seed": seed, "budget": budget[kind], "th": th})
        for target, size in sizes.get(kind, {}).items():
            for _ in range(power_reps.get(kind, 0)):
                seed += 1
                for p in paradigms:
                    tasks.append({"scenario": "power_mined", "family": f"mine/{p}/{kind}", "market": kind,
                                  "paradigm": p, "years": years, "trend": size, "seed": seed, "budget": budget[kind],
                                  "target": target, "true_edge": truth[(kind, target)], "th": th})
    return tasks


def benchmark(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Paradigms side by side on the same markets and budgets.

    Args:
        runs: Outputs of ``mine_one``.

    Returns:
        One row per family and scenario (and true edge): ``n``, ``admitted``, ``rate``, the median in-sample best
        edge, the median luck threshold, the share of bests that used each component, and the median seconds.
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((r["family"], r["scenario"], r.get("true_edge")), []).append(r)
    rows = []
    for (family, scenario, edge), rs in sorted(groups.items(), key=lambda x: (x[0][0], x[0][1], x[0][2] or 0)):
        found = [r["mined"] for r in rs if r["mined"].get("edge") is not None]

        def share(key: str, value: str, found: list[dict[str, Any]] = found) -> float:
            return float(np.mean([f[key] != value for f in found])) if found else float("nan")

        rows.append({
            "family": family, "scenario": scenario, "true_edge": edge, "n": len(rs),
            "admitted": sum(r["admitted"] for r in rs), "rate": float(np.mean([r["admitted"] for r in rs])),
            "best_edge": float(np.median([f["edge"] for f in found])) if found else None,
            "sr0": float(np.nanmedian([np.nan if f["sr0"] is None else f["sr0"] for f in found])) if found else None,
            "formula_share": float(np.mean([f["signal"] == "formula" for f in found])) if found else None,
            "filter_share": share("filter", "none"), "exit_share": share("exit", "none"),
            "session_share": share("session", "all"), "sizing_share": share("sizing", "none"),
            "nodes": float(np.median([f["nodes"] for f in found])) if found else None,
            "seconds": float(np.median([f.get("total_s", 0) for f in found])) if found else None,
            "failed": dict(pd.Series([r["failed_stage"] or "admitted" for r in rs]).value_counts()),
        })
    return rows


def headline(report: dict[str, Any]) -> str:
    """The mining calibration in two sentences: noise by family, then power.

    Args:
        report: A mining calibration report.

    Returns:
        Text.
    """
    noise = report["noise"]
    k = sum(r["admitted"] for r in noise)
    n = sum(r["n"] for r in noise)
    lo, hi = cal.wilson(k, n)
    worst = max(noise, key=lambda r: r["rate"]) if noise else None
    text = (f"At mining scale ({', '.join(f'{m} {b:,}' for m, b in report['settings']['budget'].items())} trials per "
            f"search), noise passes {k / max(n, 1):.1%} of the time (95% CI {lo:.1%}-{hi:.1%}, {n} runs"
            + (f"; worst family {worst['family']} {worst['rate']:.0%}" if worst else "") + ").")
    power = sorted(report["power"], key=lambda p: (p["family"], p["true_edge"] or 0))
    if power:
        text += " Planted edges caught: " + "; ".join(
            f"{p['family']} at edge {p['true_edge']:.2f} {p['rate']:.0%}" for p in power) + "."
    return text


def run_study(
    base_seed: int = 400_000,
    paradigms: tuple[str, ...] = PARADIGMS,
    budget: dict[str, int] | None = None,
    noise_reps: dict[str, int] | None = None,
    power_reps: dict[str, int] | None = None,
    edges: tuple[float, ...] = (0.5, 1.0),
    workers: int | None = None,
    log: Callable[[str], None] = print,
    actor: str = "user",
) -> dict[str, Any]:
    """Run the mining calibration and save its report next to the other calibration studies.

    Args:
        base_seed: Market seeds start at ``base_seed + 1``; confirm a choice on another.
        paradigms: Paradigms to compare.
        budget: Trials per search by market type (missing types take ``BUDGET``).
        noise_reps: Noise markets per market type (0 skips a type).
        power_reps: Planted markets per type and edge (0 skips power for a type).
        edges: Target true edges of the planted markets.
        workers: Processes; None uses all cores but one.
        log: Progress callback.
        actor: Who ran it.

    Returns:
        The report: ``id``, ``kind`` (``mining``), ``created_at``, ``thresholds_version``, ``thresholds``,
        ``settings``, ``noise``, ``power``, ``control``, ``fpr_overall`` (``calibration.summarise`` on the runs),
        ``benchmark``, ``headline``, ``runs`` (without trial records) and ``elapsed_s``.
    """
    started = time.perf_counter()
    code = code_version()
    version, th = thresholds.current()
    run_id = uuid.uuid4().hex[:8]
    budget = {**BUDGET, **(budget or {})}
    noise_reps = {**NOISE_REPS, **(noise_reps or {})}
    power_reps = {**POWER_REPS, **(power_reps or {})}
    n_workers = workers or max(1, (os.cpu_count() or 2) - 1)
    runs: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        sizes: dict[str, dict[float, float]] = {}
        truth: dict[tuple[str, float], float] = {}
        if power_reps.get("bars"):
            sizes["bars"] = cal.trend_sizes("trend_sma", edges, 10)
            truth.update({("bars", t): cal.true_edge("trend_sma", x) for t, x in sizes["bars"].items()})
        if power_reps.get("panel"):
            sizes["panel"] = cal.trend_sizes("portfolio/tsmom", edges, 8)
            truth.update({("panel", t): cal.true_edge("portfolio/tsmom", x) for t, x in sizes["panel"].items()})
        if power_reps.get("xs30"):
            fam = POWER_FAMILY["xs30"]
            sizes["xs30"] = cal.xs_sizes(fam, edges, pool)
            got = cal.xs_true_edges([(fam, x) for x in sizes["xs30"].values()], pool)
            truth.update({("xs30", t): got[(fam, x)] for t, x in sizes["xs30"].items()})
        log(f"planted sizes {sizes}; true edges {({f'{k[0]}@{k[1]}': round(v, 2) for k, v in truth.items()})}")
        tasks = tasks_for(th, base_seed, paradigms, budget, noise_reps, power_reps, sizes, truth)
        log(f"{len(tasks)} mining runs on {n_workers} processes")
        # Slowest market first, so the pool doesn't end on a tail of long runs.
        order = {"xs30": 0, "panel": 1, "bars": 2}
        futures = [pool.submit(mine_one, t) for t in sorted(tasks, key=lambda t: order[t["market"]])]
        for i, fut in enumerate(as_completed(futures), 1):
            runs.append(fut.result())
            if i % 25 == 0 or i == len(tasks):
                log(f"  {i}/{len(tasks)} runs done ({time.perf_counter() - started:.0f} s)")
        truth_rows, truth_records = admitted_noise(
            {"runs": runs, "settings": {"budget": budget}, "thresholds": th}, pool, log)

    trials = []
    for i, r in enumerate(runs):
        q = f"calib:{run_id}:{r['scenario']}:{r['family']}:{i}"
        trials.extend({**rec, "question": q, "family": r["family"], "symbol": "SYNTH", "source": "calibration"}
                      for rec in r.pop("miner_records"))
        trials.extend({**rec, "question": q, "family": r["family"], "strategy": r["family"], "symbol": "SYNTH",
                       "source": "calibration"} for rec in r.pop("records"))
    report = {
        "id": run_id,
        "kind": "mining",
        "created_at": naive_utc().isoformat(timespec="seconds"),
        "thresholds_version": version,
        "thresholds": th,
        "settings": {"paradigms": list(paradigms), "budget": budget, "noise_reps": noise_reps,
                     "power_reps": power_reps, "edges": list(edges), "years": MARKETS,
                     "planted_sizes": {k: {str(t): v for t, v in d.items()} for k, d in sizes.items()},
                     "true_edges": {f"{k}@{t}": v for (k, t), v in truth.items()}, "n_runs": len(runs),
                     "n_trials": len(trials), "base_seed": base_seed, "code": code},
        **cal.summarise(runs, th),
        "benchmark": benchmark(runs),
        "admitted_noise": truth_rows,
        "truth": {"years": TRUTH_YEARS, "seeds": TRUTH_SEEDS, "base_seed": TRUTH_BASE},
        "runs": [{k: r[k] for k in ("scenario", "family", "market", "paradigm", "years", "trend", "seed", "values",
                                    "admitted", "failed_stage", "mined", "dsr_by", "sr0_by", "dsr_inputs",
                                    "true_edge") if k in r}
                 for r in runs],
        "elapsed_s": round(time.perf_counter() - started, 1),
    }
    trials.extend({**rec, "question": f"calib:{run_id}:truth", "family": "mine/truth", "symbol": "SYNTH",
                   "source": "calibration"} for rec in truth_records)
    report["settings"]["n_trials"] = len(trials)
    report["headline"] = headline(report)
    path = config.reports_dir() / "calibration" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(report)))  # saved first: the runs survive a ledger write that fails
    for k in range(0, len(trials), 100_000):
        ledger.record_trials(trials[k:k + 100_000])
    audit.log("gauntlet.calibrate", target=run_id, actor=actor,
              details={"kind": "mining", "runs": len(runs), "trials": len(trials), "fpr": report["fpr_overall"]})
    return clean(report)


def add_truth(run_id: str, workers: int | None = None, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Measure the true edges of a saved study's admitted noise runs, save them in its report and record the trials.

    Args:
        run_id: Mining calibration report id.
        workers: Processes; None uses all cores but one.
        log: Progress callback.

    Returns:
        The ``admitted_noise`` rows.
    """
    rep = cal.load_report(run_id)
    with ProcessPoolExecutor(max_workers=workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        rows, records = admitted_noise(rep, pool, log)
    rep["admitted_noise"] = rows
    rep["truth"] = {"years": TRUTH_YEARS, "seeds": TRUTH_SEEDS, "base_seed": TRUTH_BASE,
                    "added_at": naive_utc().isoformat(timespec="seconds")}
    path = config.reports_dir() / "calibration" / f"{run_id}.json"
    path.write_text(json.dumps(clean(rep)))
    ledger.record_trials([{**rec, "question": f"calib:{run_id}:truth", "family": "mine/truth", "symbol": "SYNTH",
                           "source": "calibration"} for rec in records])
    audit.log("gauntlet.calibrate", target=run_id, actor="cli",
              details={"kind": "mining_truth", "admitted_noise": len(rows), "trials": len(records)})
    return rows


def dsr_at(inputs: dict[str, Any], n_factor: float, dpy: int) -> float:
    """The Deflated Sharpe of a mined best if its search had counted ``n_factor`` times as many trials.

    Args:
        inputs: A run's ``dsr_inputs`` (daily Sharpe, length, moments, the spread used, N).
        n_factor: Multiplier on N.
        dpy: Days per year of the market.

    Returns:
        The DSR.
    """
    n = max(1, int(round(inputs["n_trials"] * n_factor)))
    sr0 = stats.expected_max_sharpe(n, inputs["var_annual"] / dpy)
    return stats.psr(inputs["sr"], sr0, int(inputs["n"]), inputs["skew"], inputs["kurt"], inputs["rho"])


def rejudge(report: dict[str, Any], changes: dict[str, Any], n_factor: float = 1.0) -> dict[str, Any]:
    """What a mining calibration would say under other thresholds or a larger N, without re-running it.

    Args:
        report: A saved mining calibration report (with ``runs``).
        changes: Threshold overrides.
        n_factor: Count each mined trial this many times in the Deflated Sharpe (1 = as run).

    Returns:
        ``calibration.summarise`` under the changes.
    """
    th = {**report["thresholds"], **changes}
    method = th.get("dsr_variance", thresholds.LEGACY["dsr_variance"])
    runs = []
    for r in report["runs"]:
        r = dict(r)
        r["values"] = {k: (float("nan") if v is None else v) for k, v in r["values"].items()}
        if r.get("dsr_inputs") and n_factor != 1.0:
            dpy = 365 if r.get("market") == "xs30" else cal.DPY
            r["dsr_by"] = {**r.get("dsr_by", {}), method: dsr_at(r["dsr_inputs"], n_factor, dpy)}
        runs.append(r)
    return clean(cal.summarise(runs, th))


def is_mining(report: dict[str, Any]) -> bool:
    """Whether a calibration report is a mining study.

    Args:
        report: Any calibration report.

    Returns:
        True for ``kind == "mining"``.
    """
    return report.get("kind") == "mining"


def worst_noise(report: dict[str, Any]) -> float:
    """The highest noise pass rate of any family in a report.

    Args:
        report: A calibration report.

    Returns:
        The rate, or NaN without noise rows.
    """
    return max((r["rate"] for r in report.get("noise", [])), default=math.nan)
