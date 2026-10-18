"""Calibration: how often the gauntlet passes pure noise, and how often it catches a real edge.

A gate is only useful if both numbers are known. The gauntlet is run, unchanged, on synthetic markets where
the truth is known:

- **Noise markets** have volatility clustering (GARCH) and fat tails (Student t), like real ones, but nothing a
  timing rule can exploit. Any candidate that passes is a false positive. Two researchers are simulated: one
  who fixed the parameters in advance (N = 1), and one who searched the whole family grid and kept the
  in-sample best (N = grid size). Both must pass noise at most ~5% of the time.
- **Planted-trend markets** add a hidden drift that flips sign at random, every six months on average. Trend
  rules can earn from it, so a candidate that passes is a true positive. Varying the drift and the history
  length gives detection power versus data length.

From the two rates the report also computes the precision of the gate against a random-admission control at
the same prior share of real edges (Zheng, 2026): a gate is worth having only if what it admits is more often
real than what a coin would admit.

Every run uses the production code path (``pipeline.evaluate``) and thresholds, and its grid is recorded in
the ledger under ``calib:`` questions, so even calibration counts its trials. Runs are independent, so they go
to a process pool; nothing in a worker touches the database.
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

from .. import audit, config, ledger
from ..backtest import metrics
from ..backtest.engine import COST_PRESETS, run
from ..backtest.strategies import REGISTRY
from ..data.bars import normalize_bars
from ..timeutil import naive_utc
from . import thresholds
from .pipeline import STAGES, build_grid, clean, column_sharpes, edge_matrix, evaluate, judge, to_daily

DPY = 252
NOISE_FAMILIES = ["trend_sma", "sma_cross", "tsmom", "donchian", "rsi_reversion"]
POWER_FAMILIES = ["trend_sma", "tsmom"]


def synthetic_bars(years: float, trend: float = 0.0, seed: int = 0, vol: float = 0.16, drift: float = 0.06,
                   duration: float = 126.0) -> pd.DataFrame:
    """Daily bars from a GARCH(1,1) market with Student-t shocks and an optional hidden trend.

    Log return per day: ``drift/252 + s_t · trend/252 + σ_t ε_t`` where ``s_t`` is ±1 and flips with
    probability ``1/duration`` each day, ``σ_t`` follows GARCH(1,1) (α 0.08, β 0.90) around ``vol``, and ``ε_t``
    is Student t with 5 degrees of freedom scaled to unit variance. 30% of the variance falls overnight.

    Args:
        years: Length in years of 252 trading days.
        trend: Size of the hidden trend, annual (0.2 = ±20% a year). 0 gives a noise market.
        seed: Random seed.
        vol: Long-run annual volatility.
        drift: Unconditional annual drift.
        duration: Mean length of a trend regime in days.

    Returns:
        Normalised bars on business days from 1990-01-02, UTC.
    """
    rng = np.random.default_rng(seed)
    n = int(years * DPY)
    a, b = 0.08, 0.90
    var_lr = vol**2 / DPY
    omega = var_lr * (1 - a - b)
    night, day = rng.standard_t(5, (2, n)) / math.sqrt(5 / 3)
    shocks = math.sqrt(0.3) * night + math.sqrt(0.7) * day
    var = np.empty(n)
    v = var_lr
    for i in range(n):
        var[i] = v
        v = omega + a * v * shocks[i] ** 2 + b * v
    sd = np.sqrt(var)
    flips = rng.random(n) < 1.0 / duration
    state = np.where(np.cumsum(flips) % 2 == 0, 1.0, -1.0) * (1.0 if rng.random() < 0.5 else -1.0)
    mean = drift / DPY + state * trend / DPY
    gap = 0.3 * mean + sd * math.sqrt(0.3) * night
    body = 0.7 * mean + sd * math.sqrt(0.7) * day
    log_r = gap + body
    close = 100.0 * np.exp(np.cumsum(log_r))
    open_ = close / np.exp(body)
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.4, n)) * sd)
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.4, n)) * sd)
    ts = pd.bdate_range("1990-01-02", periods=n, tz="UTC")
    return normalize_bars(pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                                        "volume": 1e6}))


def true_edge(family: str, trend: float, years: float = 400.0, seed: int = 12345) -> float:
    """Long-run edge Sharpe of a family's default rule in a planted-trend market: the truth power is measured at.

    Args:
        family: Strategy registry key.
        trend: Hidden trend size (see ``synthetic_bars``).
        years: Simulation length; 400 years gives a standard error near 0.05.
        seed: Random seed.

    Returns:
        Annual edge Sharpe after ETF costs.
    """
    bars = synthetic_bars(years, trend, seed)
    strat = REGISTRY[family]
    res = run(bars, strat.positions(bars), COST_PRESETS["etf"], with_trades=False)
    e, _ = metrics.edge(res.returns.to_numpy(), res.benchmark.to_numpy())
    return float(metrics.sharpe(e, None, DPY))


def _one(task: dict[str, Any]) -> dict[str, Any]:
    """Run the gauntlet once on a synthetic market. Executed in a worker process.

    Args:
        task: ``scenario`` (``noise_fixed``, ``noise_tuned`` or ``power``), ``family``, ``years``, ``trend``,
            ``seed`` and ``th`` (thresholds).

    Returns:
        The task plus ``values`` (stage measurements), ``passed`` (per stage), ``admitted``, ``failed_stage``,
        ``params`` and compact grid ``records`` for the ledger.
    """
    th = task["th"]
    bars = synthetic_bars(task["years"], task["trend"], task["seed"])
    strat = REGISTRY[task["family"]]
    model = COST_PRESETS["etf"]
    sealed = bars["ts"].iloc[-1] + pd.Timedelta(days=1)
    params = strat.defaults()
    selection: list[float] = []
    counted = False
    if task["scenario"] == "noise_tuned":
        # A researcher who searched the whole grid and kept the in-sample best, honestly counting every try.
        grid = build_grid(strat, params, int(th["grid_max"]), task["seed"])
        cols, bench = [], None
        for cfg in grid:
            res = run(bars, strat.positions(bars, cfg), model, with_trades=False)
            cols.append(to_daily(res.returns.to_numpy(), bars["ts"]).to_numpy())
            bench = res.benchmark.to_numpy() if bench is None else bench
        e, _ = edge_matrix(np.column_stack(cols), bench)
        sr = column_sharpes(e) * math.sqrt(DPY)
        params = grid[int(np.nanargmax(sr))]
        selection, counted = [float(x) for x in sr[np.isfinite(sr)]], True
    out = evaluate(bars, strat, params, model, None, dpy=DPY, sealed_from=sealed, th=th, selection=selection,
                   candidate_counted=counted, seed=task["seed"])
    keep = ("sharpe", "cagr", "max_dd", "n_obs", "edge_sharpe")
    return {
        **{k: v for k, v in task.items() if k != "th"},
        "params": params,
        "values": out["values"],
        "passed": {s.key: s.passed for s in out["stages"]},
        "failed_stage": out["failed_stage"],
        "admitted": out["failed_stage"] is None,
        "records": [{**r, "metrics": {k: r["metrics"].get(k) for k in keep}} for r in out["records"]],
    }


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion.

    Args:
        k: Successes.
        n: Trials.
        z: Normal quantile (1.96 for 95%).

    Returns:
        ``(low, high)``; ``(0, 1)`` when ``n`` is 0.
    """
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def summarise(runs: list[dict[str, Any]], th: dict[str, Any]) -> dict[str, Any]:
    """Turn calibration runs into false-positive rates, power, per-stage pass rates and the admission control.

    Runs are re-judged with ``th``, so the same runs can show what other thresholds would do.

    Args:
        runs: Outputs of ``_one``.
        th: Thresholds to judge with.

    Returns:
        ``noise`` rows (family, scenario, n, admitted, rate with 95% interval, stage pass rates), ``power`` rows
        (family, trend, true edge, years, n, admitted, rate, interval, stage pass rates) and ``control`` rows
        (prior share of real edges, gate precision, random-admission precision, lift).
    """
    def verdict_of(r: dict[str, Any]) -> dict[str, bool | None]:
        return judge(r["values"], th)

    def group(rows: list[dict[str, Any]]) -> dict[str, Any]:
        verdicts = [verdict_of(r) for r in rows]
        admitted = sum(all(v is not False for v in vd.values()) for vd in verdicts)
        lo, hi = wilson(admitted, len(rows))
        stage_rates = {key: float(np.mean([vd[key] is not False for vd in verdicts])) for key, _ in STAGES}
        return {"n": len(rows), "admitted": admitted, "rate": admitted / len(rows) if rows else float("nan"),
                "low": lo, "high": hi, "stages": stage_rates}

    noise, power = [], []
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for r in runs:
        groups.setdefault((r["scenario"], r["family"], r["years"], r["trend"]), []).append(r)
    for (scenario, family, years, trend), rows in sorted(groups.items()):
        g = group(rows)
        if scenario.startswith("noise"):
            noise.append({"family": family, "scenario": scenario, "years": years, **g})
        else:
            power.append({"family": family, "trend": trend, "true_edge": rows[0].get("true_edge"), "years": years,
                          **g})

    noise_runs = [r for r in runs if r["scenario"].startswith("noise")]
    fpr = sum(all(v is not False for v in verdict_of(r).values()) for r in noise_runs) / max(len(noise_runs), 1)
    control = []
    for p in power:
        for prior in (0.05, 0.2):
            tp, fp = prior * p["rate"], (1 - prior) * fpr
            precision = tp / (tp + fp) if tp + fp > 0 else float("nan")
            control.append({"family": p["family"], "years": p["years"], "true_edge": p["true_edge"], "prior": prior,
                            "power": p["rate"], "fpr": fpr, "precision": precision, "random_precision": prior,
                            "lift": precision / prior if math.isfinite(precision) else float("nan")})
    return {"noise": noise, "power": power, "control": control, "fpr_overall": fpr}


def run_calibration(
    noise_reps: int = 100,
    power_reps: int = 40,
    years: tuple[float, ...] = (5, 10, 20, 30),
    noise_years: float = 20,
    edges: tuple[float, ...] = (0.5, 1.0),
    workers: int | None = None,
    log: Callable[[str], None] = print,
    actor: str = "user",
) -> dict[str, Any]:
    """Run the calibration study and save its report.

    Args:
        noise_reps: Runs per family and researcher type on noise markets.
        power_reps: Runs per family, edge size and history length on planted-trend markets.
        years: History lengths for the power curves.
        noise_years: History length of the noise markets.
        edges: Target long-run edge Sharpe of the default trend rule in the planted markets.
        workers: Worker processes; None uses all cores but one.
        log: Progress callback.
        actor: Who ran it, for the audit log.

    Returns:
        The report: ``id``, ``created_at``, ``thresholds_version``, ``settings``, ``noise``, ``power``,
        ``control``, ``fpr_overall``, ``headline`` and ``elapsed_s``.
    """
    started = time.perf_counter()
    version, th = thresholds.current()
    run_id = uuid.uuid4().hex[:8]

    # Find the trend size that gives each target edge for the default trend rule (edge grows with trend).
    sizes = {}
    for target in edges:
        lo, hi = 0.0, 1.0
        for _ in range(10):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if true_edge("trend_sma", mid) < target else (lo, mid)
        sizes[target] = round((lo + hi) / 2, 4)
    truth = {(f, t): true_edge(f, sizes[t]) for f in POWER_FAMILIES for t in edges}
    log(f"planted trend sizes {sizes}; true edges {({f'{k[0]}@{k[1]}': round(v, 2) for k, v in truth.items()})}")

    tasks = []
    seed = 1_000
    for family in NOISE_FAMILIES:
        for scenario in ("noise_fixed", "noise_tuned"):
            for _ in range(noise_reps):
                seed += 1
                tasks.append({"scenario": scenario, "family": family, "years": noise_years, "trend": 0.0,
                              "seed": seed, "th": th})
    for family in POWER_FAMILIES:
        for target in edges:
            for y in years:
                for _ in range(power_reps):
                    seed += 1
                    tasks.append({"scenario": "power", "family": family, "years": y, "trend": sizes[target],
                                  "target": target, "true_edge": truth[(family, target)], "seed": seed, "th": th})

    runs: list[dict[str, Any]] = []
    n_workers = workers or max(1, (os.cpu_count() or 2) - 1)
    log(f"{len(tasks)} gauntlet runs on {n_workers} processes")
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(_one, t) for t in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            runs.append(fut.result())
            if i % 50 == 0 or i == len(tasks):
                log(f"  {i}/{len(tasks)} runs done ({time.perf_counter() - started:.0f} s)")

    trials = []
    for i, r in enumerate(runs):
        family = r["family"]
        question = f"calib:{run_id}:{r['scenario']}:{family}:{i}"
        trials.extend({**rec, "question": question, "family": family, "strategy": family, "symbol": "SYNTH",
                       "source": "calibration"} for rec in r.pop("records"))
    ledger.record_trials(trials)

    report = {
        "id": run_id,
        "created_at": naive_utc().isoformat(timespec="seconds"),
        "thresholds_version": version,
        "thresholds": th,
        "settings": {"noise_reps": noise_reps, "power_reps": power_reps, "years": list(years),
                     "noise_years": noise_years, "edges": list(edges), "trend_sizes": {str(k): v for k, v in
                                                                                      sizes.items()},
                     "n_runs": len(runs), "n_trials": len(trials)},
        **summarise(runs, th),
        "runs": [{k: r[k] for k in ("scenario", "family", "years", "trend", "seed", "values", "admitted",
                                    "failed_stage")} for r in runs],
        "elapsed_s": round(time.perf_counter() - started, 1),
    }
    report["headline"] = headline(report)
    path = config.reports_dir() / "calibration" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(report)))
    audit.log("gauntlet.calibrate", target=run_id, actor=actor,
              details={"fpr": report["fpr_overall"], "runs": len(runs), "trials": len(trials)})
    return clean(report)


def headline(report: dict[str, Any]) -> str:
    """The calibration result in one sentence, as the M3 exit criterion asks for.

    Args:
        report: A calibration report with ``noise`` and ``power`` rows.

    Returns:
        "With X years of data, the gauntlet detects an edge Sharpe of S Y% of the time and passes noise Z% of
        the time."
    """
    noise = report["noise"]
    k = sum(r["admitted"] for r in noise)
    n = sum(r["n"] for r in noise)
    lo, hi = wilson(k, n)
    parts = []
    for p in sorted(report["power"], key=lambda r: (r["true_edge"] or 0, r["years"])):
        if p["family"] == "trend_sma":
            parts.append(f"{p['years']:g} years: {p['rate']:.0%} at edge {p['true_edge']:.2f}")
    return (f"Noise passes {k / max(n, 1):.1%} of the time (95% CI {lo:.1%}–{hi:.1%}, {n} runs). "
            f"Detection of a planted trend edge (trend rule) — " + "; ".join(parts) + ".")


def list_reports() -> list[dict[str, Any]]:
    """Saved calibration reports, newest first, without their per-run detail.

    Returns:
        Report summaries.
    """
    folder = config.reports_dir() / "calibration"
    if not folder.exists():
        return []
    out = []
    for path in folder.glob("*.json"):
        rep = json.loads(path.read_text())
        rep.pop("runs", None)
        out.append(rep)
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


def load_report(run_id: str) -> dict[str, Any]:
    """One calibration report, with its per-run detail.

    Args:
        run_id: Report id.

    Returns:
        The report.

    Raises:
        FileNotFoundError: Unknown report.
    """
    path = config.reports_dir() / "calibration" / f"{run_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no calibration report {run_id}")
    return json.loads(path.read_text())


def rejudge(run_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """What a saved calibration would have said under other thresholds. Nothing is re-run or saved.

    Args:
        run_id: Report id.
        changes: Threshold overrides.

    Returns:
        ``noise``, ``power``, ``control`` and ``fpr_overall`` under the changed thresholds.

    Raises:
        FileNotFoundError: Unknown report.
    """
    rep = load_report(run_id)
    th = {**rep["thresholds"], **changes}
    runs = rep["runs"]
    truth = {(p["family"], p["trend"]): p["true_edge"] for p in rep["power"]}
    for r in runs:
        r["values"] = {k: (float("nan") if v is None else v) for k, v in r["values"].items()}
        r["true_edge"] = truth.get((r["family"], r["trend"]))
    return clean(summarise(runs, th))
