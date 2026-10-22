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

Portfolio strategies are calibrated the same way on synthetic eight-asset panels (a common market factor plus
GARCH shocks per asset; planted trends flip independently per asset).

Cross-sectional crypto rules (M3c) are calibrated on synthetic crypto cross-sections where coins list and
delist, the universe is the top 30 or 100 by trailing dollar volume, and positions drift (see
``portfolio.synthetic.synthetic_crypto_panel``). Their noise markets have zero expected return for every coin,
so every rule, low volatility included, has a true edge of exactly zero. Power is measured on planted momentum
(a hidden trend per coin) for the momentum rule and on a planted size premium for the size rule. Families are
named ``xs<top>/<strategy>``.

From the two rates the report also computes the precision of the gate against a random-admission control at
the same prior share of real edges (Zheng, 2026): a gate is worth having only if what it admits is more often
real than what a coin would admit.

Intraday rules (M9, decision 0015) are calibrated on synthetic hourly crypto markets with an intraday volatility
profile (``intraday.synthetic``), judged with crypto costs and 365 days a year. Their families are named
``hourly/<strategy>``; power is measured on planted intraday momentum for the momentum rule.

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

from .. import audit, code_version, config, ledger
from ..backtest import metrics
from ..backtest.engine import COST_PRESETS, run
from ..backtest.strategies import REGISTRY
from ..data.bars import normalize_bars
from ..portfolio.engine import run_portfolio
from ..portfolio.strategies import REGISTRY as PORTFOLIOS
from ..portfolio.synthetic import synthetic_crypto_panel, synthetic_panel
from ..timeutil import naive_utc
from . import stats, thresholds
from .pipeline import STAGES, build_grid, clean, column_sharpes, deflate, edge_matrix, evaluate, judge, regime_basis
from .subjects import BarSubject, PanelSubject

DPY = 252
NOISE_FAMILIES = ["trend_sma", "sma_cross", "tsmom", "donchian", "rsi_reversion"]
POWER_FAMILIES = ["trend_sma", "tsmom"]
# Portfolio families are named "portfolio/<key>". Only timing rules are calibrated on noise: volatility-scaling
# rules (risk parity, volatility management) have a real edge in markets with volatility clustering.
PORTFOLIO_NOISE = ["portfolio/tsmom", "portfolio/trend_filter", "portfolio/xs_momentum"]
PORTFOLIO_POWER = ["portfolio/tsmom", "portfolio/trend_filter"]
PORTFOLIO_YEARS = 18  # about the research length of the multi-asset universe
PORTFOLIO_ASSETS = 8
XS_STRATEGIES = ["xs_weekly_momentum", "xs_reversal", "xs_low_vol", "xs_small_size"]
XS_POWER = ["xs_weekly_momentum", "xs_small_size"]  # planted momentum, planted size premium
XS_YEARS = 5.6  # the research length of the crypto universes (2019-02-14 to the lockbox)
XS_COINS = {30: 120, 100: 300}  # universe size -> coins that ever trade in the synthetic market
XS_TRUTH_YEARS, XS_TRUTH_SEEDS = 50.0, 8  # true edges average eight 50-year markets (standard error near 0.05)
# Pre-registered batches for the variance study: every rule runs its defaults on the same market. The crypto batch
# mixes weekly rules with daily reversal, whose costs alone push its edge far below the others.
BATCHES = {
    "single": NOISE_FAMILIES,
    "portfolio": PORTFOLIO_NOISE,
    "xs30": [f"xs30/{k}" for k in XS_STRATEGIES],
    "xs100": [f"xs100/{k}" for k in XS_STRATEGIES],
}
STUDY_METHODS = (*stats.VARIANCE_METHODS, "positive_gross")  # positive_gross is measured, not offered
HOURLY_STRATEGIES = ["intraday_momentum", "range_breakout"]
HOURLY_POWER = ["intraday_momentum"]  # planted intraday momentum
# Power is measured at the setting that matches the planted structure (the first and last four hours); the one-hour
# default can't pay its costs from a four-hour effect, and a one-hour effect fails the plateau stage by design.
HOURLY_POWER_PARAMS = {"window": 4, "hold": 4, "vol_filter": 0}
HOURLY_YEARS = 7.1  # the research length of Binance's hourly BTC and ETH bars (2017-08 to the lockbox)
HOURLY_DPY = 365


def _xs_parts(family: str) -> tuple[int, str]:
    """Universe size and strategy key of a cross-sectional calibration family.

    Args:
        family: ``xs<top>/<strategy>``, e.g. ``"xs30/xs_weekly_momentum"``.

    Returns:
        ``(top, strategy)``.
    """
    head, key = family.split("/", 1)
    return int(head[2:]), key


def xs_panel(family: str, years: float, planted: float, seed: int):
    """The synthetic crypto cross-section a cross-sectional family is calibrated on.

    Args:
        family: ``xs<top>/<strategy>``.
        years: Length in years.
        planted: Planted edge: the hidden trend for every rule but size, the size premium for the size rule.
        seed: Random seed.

    Returns:
        A dynamic ``Panel``.
    """
    top, key = _xs_parts(family)
    size = key == "xs_small_size"
    return synthetic_crypto_panel(years, XS_COINS[top], top, trend=0.0 if size else planted,
                                  size_premium=planted if size else 0.0, seed=seed)


def _xs_truth_one(args: tuple[str, float, int]) -> float:
    """Edge Sharpe of a cross-sectional family's default rule on one long synthetic market (a pool worker).

    Args:
        args: ``(family, planted, seed)``.

    Returns:
        Annual edge Sharpe after costs.
    """
    family, planted, seed = args
    panel = xs_panel(family, XS_TRUTH_YEARS, planted, seed)
    res = run_portfolio(panel, PORTFOLIOS[_xs_parts(family)[1]].weights(panel))
    e, _ = metrics.edge(res.net, res.benchmark)
    return float(metrics.sharpe(e, None, panel.dpy))


def xs_true_edges(pairs: list[tuple[str, float]], pool: ProcessPoolExecutor) -> dict[tuple[str, float], float]:
    """True edges of cross-sectional families at planted sizes, each the mean over ``XS_TRUTH_SEEDS`` markets.

    Args:
        pairs: ``(family, planted)`` to evaluate.
        pool: Process pool to spread the markets over.

    Returns:
        ``{(family, planted): edge}``.
    """
    jobs = [(f, x, 777_000 + k) for f, x in pairs for k in range(XS_TRUTH_SEEDS)]
    values = list(pool.map(_xs_truth_one, jobs))
    out: dict[tuple[str, float], list[float]] = {}
    for (f, x, _), v in zip(jobs, values, strict=True):
        out.setdefault((f, x), []).append(v)
    return {k: float(np.mean(v)) for k, v in out.items()}


def xs_sizes(family: str, edges: tuple[float, ...], pool: ProcessPoolExecutor) -> dict[float, float]:
    """Planted sizes that give a cross-sectional family each target true edge, by interpolating a grid.

    Args:
        family: ``xs<top>/<strategy>``.
        edges: Target edge Sharpes.
        pool: Process pool.

    Returns:
        ``{target: planted size}``.
    """
    grid = [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]
    truth = xs_true_edges([(family, x) for x in grid], pool)
    curve = np.maximum.accumulate(np.array([truth[(family, x)] for x in grid]))  # edge grows with the size
    return {t: round(float(np.interp(t, curve, grid)), 4) for t in edges}


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
        family: Strategy registry key, ``portfolio/<key>`` for a portfolio strategy on a synthetic panel, or
            ``hourly/<key>`` for an intraday rule on a synthetic hourly market.
        trend: Hidden trend size (see ``synthetic_bars`` and ``synthetic_panel``), or the planted intraday momentum.
        years: Simulation length; 400 years gives a standard error near 0.05 (panels use at most 150, hourly markets
            at most 120).
        seed: Random seed.

    Returns:
        Annual edge Sharpe after ETF costs (crypto costs for cross-sectional families).
    """
    if family.startswith("hourly/"):
        from ..intraday.synthetic import synthetic_hourly

        hb = synthetic_hourly(min(years, 120.0), trend, seed)
        res_h = run(hb, REGISTRY[family.split("/", 1)[1]].positions(hb, HOURLY_POWER_PARAMS), COST_PRESETS["crypto"],
                    with_trades=False)
        from .subjects import to_daily

        e, _ = metrics.edge(to_daily(res_h.returns.to_numpy(), hb["ts"]).to_numpy(),
                            to_daily(res_h.benchmark.to_numpy(), hb["ts"]).to_numpy())
        return float(metrics.sharpe(e, None, HOURLY_DPY))
    if family.startswith("xs"):
        return float(np.mean([_xs_truth_one((family, trend, 777_000 + k)) for k in range(XS_TRUTH_SEEDS)]))
    if family.startswith("portfolio/"):
        panel = synthetic_panel(min(years, 150.0), PORTFOLIO_ASSETS, trend, seed)
        res_p = run_portfolio(panel, PORTFOLIOS[family.split("/", 1)[1]].weights(panel))
        e, _ = metrics.edge(res_p.net, res_p.benchmark)
        return float(metrics.sharpe(e, None, DPY))
    bars = synthetic_bars(years, trend, seed)
    strat = REGISTRY[family]
    res = run(bars, strat.positions(bars), COST_PRESETS["etf"], with_trades=False)
    e, _ = metrics.edge(res.returns.to_numpy(), res.benchmark.to_numpy())
    return float(metrics.sharpe(e, None, DPY))


def trend_sizes(family: str, edges: tuple[float, ...], steps: int) -> dict[float, float]:
    """Planted trend sizes that give a family's default rule each target true edge, by bisection on [0, 1].

    Args:
        family: ``trend_sma`` for single instruments or ``portfolio/tsmom`` for panels (see ``true_edge``).
        edges: Target edge Sharpes.
        steps: Bisection steps (10 gives a size to about 0.001).

    Returns:
        ``{target: trend size}``.
    """
    sizes = {}
    for target in edges:
        lo, hi = 0.0, 1.0
        for _ in range(steps):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if true_edge(family, mid) < target else (lo, mid)
        sizes[target] = round((lo + hi) / 2, 4)
    return sizes


def make_subject(task: dict[str, Any]) -> BarSubject | PanelSubject:
    """The synthetic market of one calibration run, as a gauntlet subject with no lockbox.

    Args:
        task: ``family`` (a single-instrument strategy key, ``portfolio/<key>`` or ``xs<top>/<key>``), ``years``,
            ``trend`` (the planted edge; the size premium for the size rule) and ``seed``.

    Returns:
        A ``BarSubject`` on synthetic bars, a ``PanelSubject`` on a synthetic eight-asset panel for a portfolio
        family, or one on a synthetic crypto cross-section for a cross-sectional family.
    """
    if task["family"].startswith("hourly/"):
        from ..intraday.synthetic import synthetic_hourly  # intraday builds on the gauntlet: one-way layers

        hb = synthetic_hourly(task["years"], task["trend"], task["seed"])
        return BarSubject(hb, task["family"].split("/", 1)[1], hb["ts"].iloc[-1] + pd.Timedelta(days=1),
                          source="synthetic", symbol="SYNTH", interval="1h", model=COST_PRESETS["crypto"], rf=None,
                          dpy=HOURLY_DPY)
    if task["family"].startswith("xs"):
        panel = xs_panel(task["family"], task["years"], task["trend"], task["seed"])
        return PanelSubject(panel, _xs_parts(task["family"])[1], panel.ts[-1] + pd.Timedelta(days=1),
                            f"synthetic_crypto{_xs_parts(task['family'])[0]}")
    if task["family"].startswith("portfolio/"):
        panel = synthetic_panel(task["years"], PORTFOLIO_ASSETS, task["trend"], task["seed"])
        return PanelSubject(panel, task["family"].split("/", 1)[1], panel.ts[-1] + pd.Timedelta(days=1),
                            "synthetic")
    bars = synthetic_bars(task["years"], task["trend"], task["seed"])
    return BarSubject(bars, task["family"], bars["ts"].iloc[-1] + pd.Timedelta(days=1), source="synthetic",
                      symbol="SYNTH", interval="1d", model=COST_PRESETS["etf"], rf=None, dpy=DPY)


def _one(task: dict[str, Any]) -> dict[str, Any]:
    """Run the gauntlet once on a synthetic market. Executed in a worker process.

    Args:
        task: ``scenario`` (``noise_fixed``, ``noise_tuned``, ``power`` or ``power_tuned``), ``family``,
            ``years``, ``trend``, ``seed`` and ``th`` (thresholds).

    Returns:
        The output of ``_finish``.
    """
    th = task["th"]
    subject = make_subject(task)
    params = {**subject.strat.defaults(), **(task.get("params") or {})}
    selection: list[float] = []
    gross: list[float] = []
    counted = False
    if task["scenario"].endswith("tuned"):
        # A researcher who searched the whole grid and kept the in-sample best, honestly counting every try.
        grid = build_grid(subject, params, int(th["grid_max"]), task["seed"])
        sims = [subject.simulate(cfg) for cfg in grid]
        e, _ = edge_matrix(np.column_stack([x.net for x in sims]), sims[0].bench)
        g, _ = edge_matrix(np.column_stack([x.gross for x in sims]), sims[0].bench)
        sr, gsr = column_sharpes(e) * math.sqrt(subject.dpy), column_sharpes(g) * math.sqrt(subject.dpy)
        params = grid[int(np.nanargmax(sr))]
        ok = np.isfinite(sr)
        selection, gross, counted = [float(x) for x in sr[ok]], [float(x) for x in gsr[ok]], True
    return _finish(task, subject, params, selection, gross, counted)


def batch_subjects(market: str, years: float, trend: float, seed: int) -> list[BarSubject | PanelSubject]:
    """The members of a pre-registered batch, every rule of ``BATCHES[market]`` on one synthetic market.

    Args:
        market: A key of ``BATCHES``: ``single`` (synthetic bars), ``portfolio`` (an eight-asset panel), ``xs30``
            or ``xs100`` (a crypto cross-section).
        years: Length in years.
        trend: Planted trend shared by every member (0 for noise). Cross-sections get planted momentum.
        seed: Random seed of the market.

    Returns:
        One subject per member, in ``BATCHES`` order.
    """
    if market.startswith("xs"):
        top = int(market[2:])
        panel = synthetic_crypto_panel(years, XS_COINS[top], top, trend=trend, seed=seed)
        return [PanelSubject(panel, _xs_parts(f)[1], panel.ts[-1] + pd.Timedelta(days=1), f"synthetic_crypto{top}")
                for f in BATCHES[market]]
    if market == "portfolio":
        panel = synthetic_panel(years, PORTFOLIO_ASSETS, trend, seed)
        return [PanelSubject(panel, f.split("/", 1)[1], panel.ts[-1] + pd.Timedelta(days=1), "synthetic")
                for f in BATCHES[market]]
    bars = synthetic_bars(years, trend, seed)
    return [BarSubject(bars, f, bars["ts"].iloc[-1] + pd.Timedelta(days=1), source="synthetic", symbol="SYNTH",
                       interval="1d", model=COST_PRESETS["etf"], rf=None, dpy=DPY) for f in BATCHES[market]]


def _batch_one(task: dict[str, Any]) -> dict[str, Any]:
    """Judge one member of a pre-registered batch, deflated against the whole batch. Executed in a worker process.

    This is ``pipeline.judge_batch`` on a synthetic market with no prior trials: every member runs its published
    defaults, and each is deflated against the edges of all of them.

    Args:
        task: ``scenario`` (``noise_batch`` or ``power_batch``), ``market``, ``member`` (index into
            ``BATCHES[market]``), ``family``, ``years``, ``trend``, ``seed`` and ``th``.

    Returns:
        The output of ``_finish``.
    """
    subjects = batch_subjects(task["market"], task["years"], task["trend"], task["seed"])
    net, gross = [], []
    for s in subjects:
        sim = s.simulate(s.strat.defaults())
        e, _ = edge_matrix((sim.net - s.rf)[:, None], sim.bench - s.rf)
        g, _ = edge_matrix((sim.gross - s.rf)[:, None], sim.bench - s.rf)
        net.append(float(column_sharpes(e)[0] * math.sqrt(s.dpy)))
        gross.append(float(column_sharpes(g)[0] * math.sqrt(s.dpy)))
    subject = subjects[task["member"]]
    return _finish(task, subject, subject.strat.defaults(), net, gross, True)


def _finish(task: dict[str, Any], subject: BarSubject | PanelSubject, params: dict[str, float], selection: list[float],
            gross: list[float], counted: bool, weight: float = 1.0) -> dict[str, Any]:
    """Run the gauntlet on a calibration subject and record the Deflated Sharpe under every variance method.

    Every stage but significance is the same whatever the method, so one run measures them all: ``dsr_by`` holds
    the DSR each method gives, and ``summarise`` swaps it in before judging.

    Args:
        task: The calibration task (``th`` holds the thresholds).
        subject: The synthetic market.
        params: Candidate parameters.
        selection: Annual edge Sharpes the candidate was chosen from (the candidate's own included when
            ``counted``).
        gross: Annual edge Sharpes before costs, aligned with ``selection``.
        counted: Whether the candidate is already in ``selection``.
        weight: How many trials each one in ``selection`` counts as in N (``mining_n_factor`` for a miner's trials).

    Returns:
        The task plus ``values`` (stage measurements), ``passed`` (per stage), ``admitted``, ``failed_stage``,
        ``params``, ``dsr_by`` and ``sr0_by`` (the DSR and the annual luck threshold per method in
        ``STUDY_METHODS``) and compact grid ``records`` for the ledger.
    """
    out = evaluate(subject, params, th=task["th"], selection=selection, candidate_counted=counted,
                   seed=task["seed"], selection_weight=[weight] * len(selection) if weight != 1.0 else None)
    dpy = subject.dpy
    cand_e = out["candidate_edge_daily"].to_numpy()
    cand_gross = next(s for s in out["stages"] if s.key == "costs").chart["gross_edge_sharpe"]
    trial = np.asarray(selection + ([] if counted else [out["values"]["edge"]]), dtype=float)
    g = np.asarray(gross + ([] if counted else [cand_gross]), dtype=float)
    ok = np.isfinite(trial)
    trial, g = trial[ok], g[ok]
    dsr_by, sr0_by = {}, {}
    w = np.full(len(trial), weight) if weight != 1.0 else None
    for method in stats.VARIANCE_METHODS:
        d = deflate(cand_e, trial, dpy, method, weight=w)
        dsr_by[method], sr0_by[method] = d["dsr"], d["sr0"] * math.sqrt(dpy)
    # Study only: the spread of the trials that earned an edge before costs, N unchanged.
    d = stats.dsr(cand_e, max(len(trial), 1), stats.trial_variance(trial[g > 0] / math.sqrt(dpy)))
    dsr_by["positive_gross"], sr0_by["positive_gross"] = d["dsr"], d["sr0"] * math.sqrt(dpy)
    keep = ("sharpe", "cagr", "max_dd", "n_obs", "edge_sharpe")
    return {
        **{k: v for k, v in task.items() if k != "th"},
        "params": params,
        "values": out["values"],
        "passed": {s.key: s.passed for s in out["stages"]},
        "failed_stage": out["failed_stage"],
        "admitted": out["failed_stage"] is None,
        "dsr_by": dsr_by,
        "sr0_by": sr0_by,
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

    Runs are re-judged with ``th``, so the same runs can show what other thresholds would do. Runs that carry
    ``dsr_by`` are judged with the Deflated Sharpe of ``th["dsr_variance"]``, so a variance method can be
    re-judged too (``STUDY_METHODS``).

    Args:
        runs: Outputs of ``_one`` or ``_batch_one``.
        th: Thresholds to judge with.

    Returns:
        ``noise`` rows (family, scenario, n, admitted, rate with 95% interval, stage pass rates), ``power`` rows
        (family, scenario, trend, true edge, years, n, admitted, rate, interval, stage pass rates) and ``control``
        rows (prior share of real edges, gate precision, random-admission precision, lift).
    """
    method = th.get("dsr_variance", thresholds.LEGACY["dsr_variance"])

    def verdict_of(r: dict[str, Any]) -> dict[str, bool | None]:
        if method in r.get("dsr_by", {}):
            return judge({**r["values"], "dsr": r["dsr_by"][method]}, th)
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
            power.append({"family": family, "scenario": scenario, "trend": trend, "true_edge": rows[0].get("true_edge"),
                          "years": years, **g})

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
    base_seed: int = 1_000,
    portfolio_reps: int = 40,
    portfolio_power_reps: int = 20,
    xs_reps: int = 0,
    xs_power_reps: int = 15,
    hourly_reps: int = 0,
    hourly_power_reps: int = 20,
) -> dict[str, Any]:
    """Run the calibration study and save its report.

    Thresholds tuned on one study should be confirmed on another with a different ``base_seed``; otherwise they
    are fitted to the same simulated markets they are judged on.

    Args:
        noise_reps: Runs per family and researcher type on noise markets.
        power_reps: Runs per family, edge size and history length on planted-trend markets.
        years: History lengths for the power curves.
        noise_years: History length of the noise markets.
        edges: Target long-run edge Sharpe of the default trend rule in the planted markets.
        workers: Worker processes; None uses all cores but one.
        log: Progress callback.
        actor: Who ran it, for the audit log.
        base_seed: First market seed; run ``i`` uses ``base_seed + i + 1``.
        portfolio_reps: Noise runs per portfolio family and researcher type (0 skips portfolios).
        portfolio_power_reps: Planted-trend runs per portfolio family, edge size and history length.
        xs_reps: Noise runs per cross-sectional crypto family, universe size and researcher type (0 skips them).
        xs_power_reps: Planted-edge runs per cross-sectional power family, edge size and history length.
        hourly_reps: Noise runs per intraday family and researcher type on hourly markets (0 skips them).
        hourly_power_reps: Planted-momentum runs per edge size for the intraday momentum rule.

    Returns:
        The report: ``id``, ``created_at``, ``thresholds_version``, ``settings``, ``noise``, ``power``,
        ``control``, ``fpr_overall``, ``headline``, ``by_regime_basis`` (``summarise`` with the regimes stage judging
        the edge and the net return, on the same runs) and ``elapsed_s``.
    """
    started = time.perf_counter()
    code = code_version()  # stamped before the runs, so later edits to the working tree can't mislabel them
    version, th = thresholds.current()
    run_id = uuid.uuid4().hex[:8]

    # Find the trend size that gives each target edge for the default trend rule (edge grows with trend).
    sizes: dict[float, float] = {}
    truth: dict[tuple[str, float], float] = {}
    if power_reps:
        sizes = trend_sizes("trend_sma", edges, 10)
        truth = {(f, t): true_edge(f, sizes[t]) for f in POWER_FAMILIES for t in edges}
        log(f"planted trend sizes {sizes}; true edges {({f'{k[0]}@{k[1]}': round(v, 2) for k, v in truth.items()})}")
    p_sizes: dict[float, float] = {}
    if portfolio_reps:
        p_sizes = trend_sizes("portfolio/tsmom", edges, 8)
        truth.update({(f, t): true_edge(f, p_sizes[t]) for f in PORTFOLIO_POWER for t in edges})
        log(f"portfolio trend sizes {p_sizes}")

    tasks = []
    seed = base_seed
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
    for family in PORTFOLIO_NOISE if portfolio_reps else []:
        for scenario in ("noise_fixed", "noise_tuned"):
            for _ in range(portfolio_reps):
                seed += 1
                tasks.append({"scenario": scenario, "family": family, "years": PORTFOLIO_YEARS, "trend": 0.0,
                              "seed": seed, "th": th})
    for family in PORTFOLIO_POWER if portfolio_reps else []:
        for target in edges:
            for y in (10, PORTFOLIO_YEARS):
                for _ in range(portfolio_power_reps):
                    seed += 1
                    tasks.append({"scenario": "power", "family": family, "years": y, "trend": p_sizes[target],
                                  "target": target, "true_edge": truth[(family, target)], "seed": seed, "th": th})

    h_sizes: dict[float, float] = {}
    if hourly_reps or hourly_power_reps:
        for key in HOURLY_POWER:
            fam = f"hourly/{key}"
            h_sizes = trend_sizes(fam, edges, 8)
            truth.update({(fam, t): true_edge(fam, h_sizes[t]) for t in edges})
        log(f"hourly planted momentum {h_sizes}")
        for key in HOURLY_STRATEGIES:
            for scenario in ("noise_fixed", "noise_tuned"):
                for _ in range(hourly_reps):
                    seed += 1
                    tasks.append({"scenario": scenario, "family": f"hourly/{key}", "years": HOURLY_YEARS,
                                  "trend": 0.0, "seed": seed, "th": th})
        for key in HOURLY_POWER:
            fam = f"hourly/{key}"
            for target in edges:
                for _ in range(hourly_power_reps):
                    seed += 1
                    tasks.append({"scenario": "power", "family": fam, "years": HOURLY_YEARS, "trend": h_sizes[target],
                                  "target": target, "true_edge": truth[(fam, target)], "seed": seed, "th": th,
                                  "params": HOURLY_POWER_PARAMS})

    runs: list[dict[str, Any]] = []
    n_workers = workers or max(1, (os.cpu_count() or 2) - 1)
    x_sizes: dict[str, dict[float, float]] = {}
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        if xs_reps:
            for key in XS_POWER:
                fam = f"xs30/{key}"
                x_sizes[fam] = xs_sizes(fam, edges, pool)
                truth.update({(fam, t): v for (_, x), v in
                              xs_true_edges([(fam, x_sizes[fam][t]) for t in edges], pool).items()
                              for t in edges if x == x_sizes[fam][t]})
            log(f"cross-sectional planted sizes {x_sizes}; true edges "
                f"{({f'{k[0]}@{k[1]}': round(v, 2) for k, v in truth.items() if k[0].startswith('xs')})}")
            for top in XS_COINS:
                for key in XS_STRATEGIES:
                    for scenario in ("noise_fixed", "noise_tuned"):
                        for _ in range(xs_reps):
                            seed += 1
                            tasks.append({"scenario": scenario, "family": f"xs{top}/{key}", "years": XS_YEARS,
                                          "trend": 0.0, "seed": seed, "th": th})
            for fam, by_target in x_sizes.items():
                for target in edges:
                    for y in (4.0, XS_YEARS):
                        for _ in range(xs_power_reps):
                            seed += 1
                            tasks.append({"scenario": "power", "family": fam, "years": y, "trend": by_target[target],
                                          "target": target, "true_edge": truth[(fam, target)], "seed": seed,
                                          "th": th})
        log(f"{len(tasks)} gauntlet runs on {n_workers} processes")
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

    report = {
        "id": run_id,
        "created_at": naive_utc().isoformat(timespec="seconds"),
        "thresholds_version": version,
        "thresholds": th,
        "settings": {"noise_reps": noise_reps, "power_reps": power_reps, "years": list(years),
                     "noise_years": noise_years, "edges": list(edges),
                     "trend_sizes": {str(k): v for k, v in sizes.items()},
                     "portfolio_trend_sizes": {str(k): v for k, v in p_sizes.items()},
                     "portfolio_reps": portfolio_reps, "portfolio_power_reps": portfolio_power_reps,
                     "xs_reps": xs_reps, "xs_power_reps": xs_power_reps,
                     "hourly_reps": hourly_reps, "hourly_power_reps": hourly_power_reps,
                     "hourly_planted_momentum": {str(k): v for k, v in h_sizes.items()},
                     "xs_planted_sizes": {f: {str(k): v for k, v in d.items()} for f, d in x_sizes.items()},
                     "n_runs": len(runs), "n_trials": len(trials), "base_seed": base_seed,
                     "code": code},
        **summarise(runs, th),
        "by_regime_basis": {b: summarise(runs, {**th, "regime_basis": b}) for b in thresholds.REGIME_BASES},
        "runs": [{k: r[k] for k in ("scenario", "family", "years", "trend", "seed", "values", "admitted",
                                    "failed_stage")} for r in runs],
        "elapsed_s": round(time.perf_counter() - started, 1),
    }
    report["headline"] = headline(report)
    path = config.reports_dir() / "calibration" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(report)))  # saved first: the runs survive a ledger write that fails
    for k in range(0, len(trials), 100_000):
        ledger.record_trials(trials[k:k + 100_000])
    audit.log("gauntlet.calibrate", target=run_id, actor=actor,
              details={"fpr": report["fpr_overall"], "runs": len(runs), "trials": len(trials)})
    return clean(report)


STUDY_REPS = {
    "single_tuned": 100,     # noise runs per single-instrument family, best of the grid
    "single_batch": 100,     # noise markets with the five single-instrument rules as one batch
    "single_power": 40,      # planted-trend runs per edge, tuned and in a batch
    "portfolio_tuned": 40,   # noise runs per portfolio family, best of the grid
    "portfolio_batch": 40,   # noise panels with the three portfolio timing rules as one batch
    "portfolio_power": 20,   # planted-trend panels per edge, tuned and in a batch
    "xs_tuned": 40,          # noise runs per crypto family and universe size, best of the grid
    "xs30_batch": 100,       # top-30 noise cross-sections with the four crypto rules as one batch
    "xs100_batch": 40,       # top-100 noise cross-sections with the four crypto rules as one batch
    "xs_power": 30,          # planted-momentum top-30 cross-sections per edge, tuned and in a batch
}


def variance_tasks(reps: dict[str, int], th: dict[str, Any], base_seed: int, sizes: dict[str, dict[float, float]],
                   truth: dict[tuple[str, float], float]) -> list[dict[str, Any]]:
    """Calibration tasks of the variance study, one per gauntlet run, each on its own market seed.

    Batch members share their market's seed; every other run has its own.

    Args:
        reps: Counts by ``STUDY_REPS`` key.
        th: Thresholds for every run.
        base_seed: First market seed; markets use ``base_seed + 1`` onwards.
        sizes: Planted sizes by power family (``trend_sma``, ``portfolio/tsmom``, ``xs30/xs_weekly_momentum``) and
            target edge.
        truth: True edge by ``(family, target edge)``.

    Returns:
        Tasks for ``_one`` (``noise_tuned``, ``power_tuned``) and ``_batch_one`` (``noise_batch``, ``power_batch``).
    """
    tasks: list[dict[str, Any]] = []
    seed = base_seed

    def batch(market: str, years: float, n: int, power: str | None) -> None:
        nonlocal seed
        if n == 0:
            return
        members = BATCHES[market]
        for target in sizes[power] if power else [None]:
            for _ in range(n):
                seed += 1
                for i, fam in enumerate(members):
                    if power and fam != power:
                        continue
                    tasks.append({"scenario": "power_batch" if power else "noise_batch", "market": market,
                                  "member": i, "family": fam, "years": years,
                                  "trend": sizes[power][target] if power else 0.0, "seed": seed, "th": th,
                                  **({"target": target, "true_edge": truth[(power, target)]} if power else {})})

    def tuned(family: str, years: float, n: int, power: bool) -> None:
        nonlocal seed
        if n == 0:
            return
        for target in sizes[family] if power else [None]:
            for _ in range(n):
                seed += 1
                tasks.append({"scenario": "power_tuned" if power else "noise_tuned", "family": family,
                              "years": years, "trend": sizes[family][target] if power else 0.0, "seed": seed,
                              "th": th, **({"target": target, "true_edge": truth[(family, target)]} if power else {})})

    for fam in NOISE_FAMILIES:
        tuned(fam, 20, reps["single_tuned"], False)
    batch("single", 20, reps["single_batch"], None)
    tuned("trend_sma", 20, reps["single_power"], True)
    batch("single", 20, reps["single_power"], "trend_sma")
    for fam in PORTFOLIO_NOISE:
        tuned(fam, PORTFOLIO_YEARS, reps["portfolio_tuned"], False)
    batch("portfolio", PORTFOLIO_YEARS, reps["portfolio_batch"], None)
    tuned("portfolio/tsmom", PORTFOLIO_YEARS, reps["portfolio_power"], True)
    batch("portfolio", PORTFOLIO_YEARS, reps["portfolio_power"], "portfolio/tsmom")
    for top in XS_COINS:
        for key in XS_STRATEGIES:
            tuned(f"xs{top}/{key}", XS_YEARS, reps["xs_tuned"], False)
    batch("xs30", XS_YEARS, reps["xs30_batch"], None)
    batch("xs100", XS_YEARS, reps["xs100_batch"], None)
    tuned("xs30/xs_weekly_momentum", XS_YEARS, reps["xs_power"], True)
    batch("xs30", XS_YEARS, reps["xs_power"], "xs30/xs_weekly_momentum")
    return tasks


def run_variance_study(
    reps: dict[str, int] | None = None,
    edges: tuple[float, ...] = (0.5, 1.0),
    workers: int | None = None,
    log: Callable[[str], None] = print,
    actor: str = "user",
    base_seed: int = 210_000,
) -> dict[str, Any]:
    """Measure how each way of estimating the Deflated Sharpe's cross-trial variance trades false positives for power.

    The Deflated Sharpe compares a candidate with the best Sharpe luck would reach among N trials, which grows
    with the spread of the trials' Sharpes. The study runs the unchanged gauntlet where that spread matters:

    - **Best of the grid** (``noise_tuned``, ``power_tuned``): a researcher kept the in-sample best of up to 300
      settings, every family as in the main calibration.
    - **Pre-registered batches** (``noise_batch``, ``power_batch``): every rule of a market type runs its defaults
      on the same market, and each is deflated against the whole batch, as ``pipeline.judge_batch`` does. The
      crypto batch mixes weekly rules with daily reversal, whose costs alone sink its edge.

    Each run records the DSR under every method in ``STUDY_METHODS``. Every other stage is unaffected, so the
    methods are compared on identical runs. Grids are recorded in the ledger under ``calib:`` questions.

    Args:
        reps: Counts by ``STUDY_REPS`` key; missing keys take their default and 0 skips a part.
        edges: Target true edge Sharpes for the planted markets.
        workers: Worker processes; None uses all cores but one.
        log: Progress callback.
        actor: Who ran it, for the audit log.
        base_seed: Market seeds start at ``base_seed + 1``. Confirm a choice on a different one.

    Returns:
        The report: ``id``, ``kind`` (``dsr_variance``), ``created_at``, ``thresholds_version``, ``thresholds``,
        ``settings``, ``by_method`` (``summarise`` under each method), ``sr0`` (median luck thresholds),
        ``noise``, ``power``, ``control`` and ``fpr_overall`` under the method in force, ``headline``, ``runs`` and
        ``elapsed_s``.
    """
    started = time.perf_counter()
    code = code_version()
    version, th = thresholds.current()
    run_id = uuid.uuid4().hex[:8]
    reps = {**STUDY_REPS, **(reps or {})}
    n_workers = workers or max(1, (os.cpu_count() or 2) - 1)
    runs: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        sizes: dict[str, dict[float, float]] = {}
        truth: dict[tuple[str, float], float] = {}
        if reps["single_power"]:
            sizes["trend_sma"] = trend_sizes("trend_sma", edges, 10)
            truth.update({("trend_sma", t): true_edge("trend_sma", x) for t, x in sizes["trend_sma"].items()})
        if reps["portfolio_power"]:
            sizes["portfolio/tsmom"] = trend_sizes("portfolio/tsmom", edges, 8)
            truth.update({("portfolio/tsmom", t): true_edge("portfolio/tsmom", x)
                          for t, x in sizes["portfolio/tsmom"].items()})
        if reps["xs_power"]:
            fam = "xs30/xs_weekly_momentum"
            sizes[fam] = xs_sizes(fam, edges, pool)
            got = xs_true_edges([(fam, x) for x in sizes[fam].values()], pool)
            truth.update({(fam, t): got[(fam, x)] for t, x in sizes[fam].items()})
        log(f"planted sizes {sizes}; true edges {({f'{k[0]}@{k[1]}': round(v, 2) for k, v in truth.items()})}")
        tasks = variance_tasks(reps, th, base_seed, sizes, truth)
        log(f"{len(tasks)} gauntlet runs on {n_workers} processes")
        futures = [pool.submit(_batch_one if t["scenario"].endswith("batch") else _one, t) for t in tasks]
        for i, fut in enumerate(as_completed(futures), 1):
            runs.append(fut.result())
            if i % 50 == 0 or i == len(tasks):
                log(f"  {i}/{len(tasks)} runs done ({time.perf_counter() - started:.0f} s)")

    trials = []
    for i, r in enumerate(runs):
        question = f"calib:{run_id}:{r['scenario']}:{r['family']}:{i}"
        trials.extend({**rec, "question": question, "family": r["family"], "strategy": r["family"],
                       "symbol": "SYNTH", "source": "calibration"} for rec in r.pop("records"))

    by_method = {m: summarise(runs, {**th, "dsr_variance": m}) for m in STUDY_METHODS}
    sr0: dict[str, dict[str, list[float]]] = {}
    for r in runs:
        for m, v in r["sr0_by"].items():
            sr0.setdefault(f"{r['scenario']}:{r['family']}", {}).setdefault(m, []).append(v)
    report = {
        "id": run_id,
        "kind": "dsr_variance",
        "created_at": naive_utc().isoformat(timespec="seconds"),
        "thresholds_version": version,
        "thresholds": th,
        "settings": {"reps": reps, "edges": list(edges), "planted_sizes": {f: {str(k): v for k, v in d.items()}
                                                                         for f, d in sizes.items()},
                     "true_edges": {f"{f}@{t}": v for (f, t), v in truth.items()}, "methods": list(STUDY_METHODS),
                     "n_runs": len(runs), "n_trials": len(trials), "base_seed": base_seed, "code": code},
        "by_method": by_method,
        "sr0": {k: {m: float(np.nanmedian(v)) for m, v in d.items()} for k, d in sr0.items()},
        **by_method[th["dsr_variance"]],
        "runs": [{k: r[k] for k in ("scenario", "family", "years", "trend", "seed", "values", "admitted",
                                    "failed_stage", "dsr_by", "sr0_by")} for r in runs],
        "elapsed_s": round(time.perf_counter() - started, 1),
    }
    report["headline"] = variance_headline(report)
    path = config.reports_dir() / "calibration" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(report)))  # saved first: the runs survive a ledger write that fails
    for k in range(0, len(trials), 100_000):
        ledger.record_trials(trials[k:k + 100_000])
    audit.log("gauntlet.calibrate", target=run_id, actor=actor,
              details={"kind": "dsr_variance", "runs": len(runs), "trials": len(trials),
                       "fpr": {m: s["fpr_overall"] for m, s in by_method.items()}})
    return clean(report)


def variance_headline(report: dict[str, Any]) -> str:
    """The variance study in one sentence per method: the worst noise family and the power in batches.

    Args:
        report: A ``run_variance_study`` report.

    Returns:
        One clause per method, joined by semicolons.
    """
    parts = []
    for m, s in report["by_method"].items():
        worst = max(s["noise"], key=lambda r: r["rate"]) if s["noise"] else None
        batch = [p for p in s["power"] if p["scenario"] == "power_batch"]
        text = f"{m}: noise {s['fpr_overall']:.1%} pooled"
        if worst:
            text += f", worst {worst['scenario']} {worst['family']} {worst['rate']:.0%}"
        if batch:
            text += ", batch power " + ", ".join(f"{p['family']} edge {p['true_edge']:.2f} {p['rate']:.0%}"
                                                 for p in sorted(batch, key=lambda p: (p["family"], p["trend"])))
        parts.append(text)
    return "; ".join(parts) + "."


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
    worst = max(noise, key=lambda r: r["rate"]) if noise else None

    def curve(family: str) -> str:
        rows = sorted((p for p in report["power"] if p["family"] == family),
                      key=lambda r: (r["true_edge"] or 0, r["years"]))
        return "; ".join(f"{p['years']:g} y at edge {p['true_edge']:.2f}: {p['rate']:.0%}" for p in rows)

    text = (f"Noise passes {k / max(n, 1):.1%} of the time (95% CI {lo:.1%}–{hi:.1%}, {n} runs"
            + (f"; worst family {worst['family']} {worst['rate']:.0%}" if worst else "") + ").")
    if any(p["family"] == "trend_sma" for p in report["power"]):
        text += f" Single-instrument trend rule detects a planted edge — {curve('trend_sma')}."
    if any(p["family"].startswith("portfolio/") for p in report["power"]):
        text += f" Portfolio trend (8 assets) — {curve('portfolio/tsmom')}."
    if any(p["family"].startswith("hourly/") for p in report["power"]):
        text += f" Intraday momentum (hourly) — {curve('hourly/intraday_momentum')}."
    if any(p["family"].startswith("xs") for p in report["power"]):
        text += (f" Crypto momentum (top 30) — {curve('xs30/xs_weekly_momentum')}."
                 f" Crypto size (top 30) — {curve('xs30/xs_small_size')}.")
    return text


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
        ValueError: The changes ask the regimes stage to judge the edge, and the report's runs predate the edge by
            regime.
    """
    rep = load_report(run_id)
    th = {**rep["thresholds"], **changes}
    runs = rep["runs"]
    if regime_basis(th) == "edge" and any("regime_edge_share" not in r["values"] for r in runs):
        raise ValueError(f"calibration {run_id} didn't measure the edge by regime; run a new calibration to judge it")
    truth = {(p["family"], p["trend"]): p["true_edge"] for p in rep["power"]}
    for r in runs:
        r["values"] = {k: (float("nan") if v is None else v) for k, v in r["values"].items()}
        r["true_edge"] = truth.get((r["family"], r["trend"]))
    return clean(summarise(runs, th))
