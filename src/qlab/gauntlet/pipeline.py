"""Run a candidate through the ten gauntlet stages, save its report card, and open the lockbox on request.

Design:

- **One fixed lockbox date** for the whole lab (see ``qlab.lockbox``). Stages 2-9 see only research data
  before it. Positions are computed on the full history (stage 1 checks they can't see the future) and then
  sliced, so indicators are warmed up at the start of the research period.
- **Edge, not raw return.** Every statistic is computed on the return beyond a static position in the
  benchmark (``metrics.edge``), so a rule that is merely 60% invested in a rising market doesn't look skilled.
- **The candidate and its family.** The candidate is evaluated with a grid over its family's whole parameter
  space (up to ``grid_max`` settings, always including the candidate's neighbours). Every grid setting is a
  ledger trial. The grid feeds walk-forward selection, the plateau test and PBO; the ledger's full set of
  trials on the instrument feeds the Deflated Sharpe Ratio.
- **Each stage answers one question,** so a failure says what's wrong: is it leaking, does it survive costs,
  does choosing parameters on the past work in the future, is it a lone peak, is it better than random
  timing, how much can it lose, is it the best of many by luck, does it only work in one kind of market, does
  it add anything, and does it hold up on data nobody has seen.
- ``evaluate`` is pure (no database), so calibration can run it on synthetic markets in parallel processes.
  ``run_gauntlet`` wraps it with the ledger, the report card and the audit log.
- Statistics use daily returns (intraday results are compounded per UTC day).
"""

from __future__ import annotations

import itertools
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from .. import audit, code_version, config, db, ledger, lockbox
from ..backtest import metrics, runner
from ..backtest.engine import CostModel, run
from ..backtest.leakage import future_shift_check
from ..backtest.strategies import REGISTRY, Strategy
from ..data import store
from ..data.ingest import SOURCES
from ..timeutil import naive_utc
from . import stats, thresholds
from .nulls import permuter, stationary_bootstrap
from .pbo import pbo

STAGES = [
    ("leakage", "Look-ahead"),
    ("costs", "After costs"),
    ("walk_forward", "Walk-forward"),
    ("plateau", "Plateau"),
    ("permutation", "Permutation"),
    ("bootstrap", "Bootstrap risk"),
    ("significance", "Deflated Sharpe & PBO"),
    ("regimes", "Regimes"),
    ("diversification", "Diversification"),
    ("lockbox", "Lockbox"),
]

Z_95 = 1.6448536269514722


@dataclass
class Stage:
    """One stage's outcome.

    Attributes:
        key: Stage id.
        name: Display name.
        passed: True, False, or None when the stage informs but can't reject (bootstrap sizing), or isn't
            available yet (lockbox sealed, no validated portfolio).
        value: Headline number.
        threshold: Rule in words, e.g. "p <= 0.01".
        summary: One sentence with the numbers.
        chart: Data for the stage's chart.
    """

    key: str
    name: str
    passed: bool | None
    value: float | None
    threshold: str
    summary: str
    chart: dict[str, Any] | None = None


def days_per_year(source: str) -> int:
    """Trading days per year for daily statistics.

    Args:
        source: Source name.

    Returns:
        365 for crypto, 252 otherwise.
    """
    return 365 if SOURCES.get(source) and SOURCES[source].asset_class == "crypto" else 252


def to_daily(returns: np.ndarray | pd.Series, ts: pd.Series) -> pd.Series:
    """Compound per-bar returns into daily returns.

    Args:
        returns: Per-bar returns.
        ts: Bar timestamps aligned with ``returns``.

    Returns:
        Daily returns indexed by UTC date. Daily bars pass through unchanged.
    """
    idx = pd.DatetimeIndex(ts).tz_convert("UTC")
    values = np.asarray(returns, dtype=float)
    day = idx.normalize()
    if day.is_unique:
        return pd.Series(values, index=day)
    return (1.0 + pd.Series(values, index=day)).groupby(level=0).prod() - 1.0


def build_grid(strat: Strategy, params: dict[str, float], cap: int, seed: int = 0) -> list[dict[str, float]]:
    """Candidate first, then settings across the family's parameter space.

    The full grid runs from each parameter's ``low`` to ``high`` in ``step`` increments. Above ``cap``
    settings, the grid keeps every setting the stages need (the candidate's neighbours one step away and the
    plane of the first two parameters through the candidate, for the heatmap), even if they alone exceed the
    cap, and fills the rest at random up to it.

    Args:
        strat: Strategy whose parameter ranges define the grid.
        params: Candidate parameters.
        cap: Most settings to return.
        seed: Seed for the random fill.

    Returns:
        Parameter dicts, candidate first, without duplicates.
    """
    if not strat.params:
        return [{}]
    names = list(strat.params)

    def cast(name: str, v: float) -> float:
        return int(round(v)) if strat.params[name].is_int else round(float(v), 10)

    cand = {k: cast(k, params[k]) for k in names}
    axes = {}
    for k, p in strat.params.items():
        n_steps = int(round((p.high - p.low) / p.step))
        values = {cast(k, p.low + i * p.step) for i in range(n_steps + 1)} | {cand[k]}
        axes[k] = sorted(values)

    def key(cfg: dict[str, float]) -> tuple:
        return tuple(cfg[k] for k in names)

    full = [dict(zip(names, vals, strict=True)) for vals in itertools.product(*(axes[k] for k in names))]
    if len(full) <= cap:
        chosen = full
    else:
        def near(cfg: dict[str, float]) -> bool:
            return all(abs(cfg[k] - cand[k]) <= strat.params[k].step + 1e-9 for k in names)

        def in_plane(cfg: dict[str, float]) -> bool:
            return all(cfg[k] == cand[k] for k in names[2:])

        must = [c for c in full if near(c) or in_plane(c)]
        rest = [c for c in full if not (near(c) or in_plane(c))]
        rng = np.random.default_rng(seed)
        fill = max(0, cap - len(must))
        picks = rng.choice(len(rest), size=min(fill, len(rest)), replace=False) if fill and rest else []
        chosen = must + [rest[i] for i in sorted(picks)]
    seen, out = {key(cand)}, [cand]
    for c in chosen:
        if key(c) not in seen:
            seen.add(key(c))
            out.append(c)
    return out


def edge_matrix(x: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Edge returns for many strategies at once (see ``metrics.edge``).

    Args:
        x: Excess returns, shape ``(T, K)``.
        b: Benchmark excess returns, shape ``(T,)``.

    Returns:
        ``(edge, beta)`` with shapes ``(T, K)`` and ``(K,)``.
    """
    bc = b - b.mean()
    var_b = float(np.mean(bc**2))
    if var_b == 0:
        return np.full_like(x, np.nan), np.full(x.shape[1], np.nan)
    beta = (x - x.mean(axis=0)).T @ bc / len(b) / var_b
    return x - np.outer(b, beta), beta


def column_sharpes(x: np.ndarray) -> np.ndarray:
    """Per-period Sharpe of each column.

    Args:
        x: Returns, shape ``(T, K)``.

    Returns:
        Shape ``(K,)``; NaN where a column has no variance.
    """
    sd = x.std(axis=0, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sd > 0, x.mean(axis=0) / sd, np.nan)


def _ann(sr: float, dpy: int) -> float:
    """Annualise a daily Sharpe ratio.

    Args:
        sr: Per-day Sharpe.
        dpy: Days per year.

    Returns:
        Annual Sharpe, or NaN.
    """
    return float(sr) * math.sqrt(dpy) if sr is not None and math.isfinite(sr) else float("nan")


def _f(x: float, fmt: str = "{:.2f}") -> str:
    """Format a number for a stage summary, with a dash for NaN.

    Args:
        x: Value.
        fmt: Format string.

    Returns:
        The formatted value.
    """
    return fmt.format(x) if x is not None and math.isfinite(x) else "–"


def crisis_windows(bench: pd.Series, n: int = 3) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
    """The benchmark's ``n`` deepest drawdowns, peak to trough, without overlap.

    Args:
        bench: Daily benchmark returns indexed by date.
        n: Number of episodes.

    Returns:
        ``(peak_date, trough_date, depth)`` tuples, deepest first.
    """
    eq = (1.0 + bench.fillna(0.0)).cumprod()
    dd = eq / eq.cummax() - 1.0
    free = pd.Series(True, index=dd.index)
    out = []
    for _ in range(n):
        cand = dd[free]
        if cand.empty or cand.min() >= -0.05:
            break
        trough = cand.idxmin()
        peak = eq[:trough].idxmax()
        recovered = eq[trough:][eq[trough:] >= eq[peak]]
        end = recovered.index[0] if len(recovered) else dd.index[-1]
        out.append((peak, trough, float(dd[trough])))
        free[peak:end] = False
    return out


def _ok(x: float | None) -> bool:
    """Whether a stage value is a usable number.

    Args:
        x: Value.

    Returns:
        True for finite numbers.
    """
    return x is not None and math.isfinite(x)


def judge(v: dict[str, float], th: dict[str, Any]) -> dict[str, bool | None]:
    """Pass or fail each stage from its measured values. The only place where thresholds decide.

    Keeping the decision apart from the measurement lets calibration re-judge thousands of simulated runs
    under other thresholds without re-running them, and lets the UI preview a threshold change.

    Args:
        v: Stage values as returned by ``evaluate`` under ``values``.
        th: Thresholds.

    Returns:
        Stage key to True (pass), False (fail) or None (can't reject: sizing, not applicable, sealed).
    """
    return {
        "leakage": bool(v["leak"]),
        "costs": _ok(v["edge"]) and v["edge"] > th["min_net_sharpe"] and _ok(v["net_cagr"]) and v["net_cagr"] > 0,
        "walk_forward": _ok(v["wf_oos"]) and v["wf_oos"] > th["wf_min_oos_sharpe"] and _ok(v["wf_ratio"])
        and v["wf_ratio"] >= th["wf_min_ratio"],
        "plateau": (v["plateau"] >= th["plateau_min"]) if _ok(v["plateau"]) else None,
        "permutation": v["perm_p"] <= th["perm_max_p"],
        "bootstrap": None,
        "significance": _ok(v["dsr"]) and v["dsr"] >= th["dsr_min"]
        and (not _ok(v["pbo"]) or v["pbo"] <= th["pbo_max"]),
        "regimes": (v["regime_share"] >= th["regime_min_share"]) if _ok(v["regime_share"]) else None,
        "diversification": (v["div_corr"] <= th["max_corr"] and v["div_gain"] >= 0) if _ok(v["div_corr"])
        and _ok(v["div_gain"]) else None,
        "lockbox": None,
    }


def evaluate(
    bars: pd.DataFrame,
    strat: Strategy,
    cand_params: dict[str, float],
    model: CostModel,
    rf: pd.Series | None,
    *,
    dpy: int,
    sealed_from: pd.Timestamp,
    th: dict[str, Any],
    selection: list[float] | None = None,
    candidate_counted: bool = False,
    portfolio: pd.Series | None = None,
    seed: int = 7,
) -> dict[str, Any]:
    """Judge a candidate on the research period. Pure: reads and writes nothing.

    The Deflated Sharpe Ratio deflates against the trials that existed when the candidate was chosen, plus the
    candidate. This run's own grid isn't among them (nobody had seen it yet); it is returned as ``records`` for
    the caller to store, and every later candidate on the instrument is deflated against it.

    Args:
        bars: Full history of normalised bars, positional index.
        strat: Strategy to judge.
        cand_params: Candidate parameters (complete).
        model: Cost model.
        rf: Risk-free return per bar, aligned with ``bars``. None means zero.
        dpy: Days per year for annualising daily statistics.
        sealed_from: First lockbox date; only earlier bars are used.
        th: Thresholds (see ``thresholds.DEFAULT``).
        selection: Annual edge Sharpes of the distinct trials the candidate was chosen from. None means none.
        candidate_counted: True when the candidate itself (same parameters, same research period) is already
            among ``selection``; otherwise it is added.
        portfolio: Daily returns of the already-validated portfolio, or None when there isn't one.
        seed: Seed for the grid fill, permutations and bootstrap.

    Returns:
        ``stages`` (list of ``Stage``), ``failed_stage``, ``research`` (first and last date), ``grid``,
        ``grid_size``, ``records`` (one trial record per grid setting, for ``ledger.record_trials``), ``n_trials``,
        ``timings`` (seconds per step), and the candidate's daily net and edge returns.

    Raises:
        ValueError: Fewer than three years of research data.
    """
    t0 = time.perf_counter()
    timings: dict[str, float] = {}

    def lap(name: str) -> None:
        nonlocal t0
        timings[name] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()

    rng = np.random.default_rng(seed)
    mask = (bars["ts"] < sealed_from).to_numpy()
    ts_r = bars["ts"][mask]
    days = to_daily(np.zeros(mask.sum()), ts_r).index
    if len(days) < 3 * dpy:
        raise ValueError("need at least three years of research data before the lockbox")
    research_bars = bars[mask].reset_index(drop=True)
    research = [str(ts_r.iloc[0].date()), str(ts_r.iloc[-1].date())]

    # Grid: every setting on the full history, sliced to the research period, as daily returns.
    grid = build_grid(strat, cand_params, int(th["grid_max"]), seed)
    cols, cand_res = [], None
    for cfg in grid:
        res = run(bars, strat.positions(bars, cfg), model, rf, with_trades=False)
        if cand_res is None:
            cand_res = res
        cols.append(to_daily(res.returns.to_numpy()[mask], ts_r).to_numpy())
    R = np.column_stack(cols)
    bench = to_daily(cand_res.benchmark.to_numpy()[mask], ts_r).to_numpy()
    rf_d = np.zeros(len(days)) if rf is None else to_daily(rf.to_numpy()[mask], ts_r).to_numpy()
    E, betas = edge_matrix(R - rf_d[:, None], bench - rf_d)
    edge_sr = column_sharpes(E)  # daily
    cand_e = E[:, 0]
    cand_edge = _ann(edge_sr[0], dpy)
    lap("grid")

    records = []
    for k, cfg in enumerate(grid):
        m = metrics.summary(pd.Series(R[:, k]), pd.Series(rf_d), dpy, benchmark=pd.Series(bench))
        records.append({"params": cfg, "start": research[0], "end": research[1], "metrics": m})
    trial_sr = np.asarray(list(selection or []) + ([] if candidate_counted else [cand_edge]), dtype=float)
    trial_sr = trial_sr[np.isfinite(trial_sr)]
    lap("metrics")

    stages: list[Stage] = []
    values: dict[str, float] = {}

    # 1. Look-ahead: changing future bars must not change any earlier position.
    leak = future_shift_check(strat, research_bars, cand_params, seed=seed)
    values["leak"] = 1.0 if leak["passed"] else 0.0
    stages.append(Stage(
        "leakage", "Look-ahead", leak["passed"], None, "no past position changes",
        "Changing future bars left every earlier position unchanged." if leak["passed"]
        else f"Position at bar {leak['first_violation']} changed when only later data changed.",
    ))
    lap("leakage")

    # 2. Costs: positive net growth and positive edge after every fee, spread and slippage.
    gross = to_daily((cand_res.gross + cand_res.cash).to_numpy()[mask], ts_r).to_numpy()
    gross_e, _ = metrics.edge(gross, bench, rf_d)
    gross_edge = _ann(stats.sharpe(gross_e), dpy)
    net_cagr = metrics.cagr(R[:, 0], dpy)
    turnover = float(np.abs(np.diff(cand_res.position.to_numpy()[mask], prepend=0.0)).sum()) / (len(days) / dpy)
    gross_alpha = float(np.nanmean(gross_e) * dpy)
    breakeven_bps = gross_alpha / turnover * 1e4 if turnover > 0 else float("inf")
    values.update(edge=cand_edge, net_cagr=net_cagr)
    stages.append(Stage(
        "costs", "After costs", None, cand_edge, f"edge Sharpe > {th['min_net_sharpe']} and CAGR > 0",
        f"Edge Sharpe {_f(cand_edge)} after costs ({_f(gross_edge)} before); net CAGR {_f(net_cagr, '{:+.1%}')}; "
        f"costs break even at {_f(breakeven_bps, '{:.0f}')} bps per trade vs {model.per_unit * 1e4:.1f} charged.",
        {"gross_edge_sharpe": gross_edge, "net_edge_sharpe": cand_edge, "net_cagr": net_cagr,
         "gross_cagr": metrics.cagr(gross, dpy), "turnover": turnover, "breakeven_bps": breakeven_bps,
         "cost_bps": model.per_unit * 1e4, "beta": float(betas[0]), "alpha": float(np.nanmean(cand_e) * dpy)},
    ))
    lap("costs")

    # 3. Walk-forward: each year, pick the grid setting with the best edge before it, then trade it that year.
    X, b = R - rf_d[:, None], bench - rf_d
    folds, oos_parts, oos_bench, is_sr = [], [], [], []
    first = max(int(len(days) * 0.4), 2 * dpy)
    for start in range(first, len(days), dpy):
        stop = min(start + dpy, len(days))
        if stop - start < dpy // 4:
            break
        e_train, _ = edge_matrix(X[:start], b[:start])
        train_sr = column_sharpes(e_train)
        pick = int(np.nanargmax(train_sr)) if np.isfinite(train_sr).any() else 0
        oos_parts.append(X[start:stop, pick])
        oos_bench.append(b[start:stop])
        is_sr.append(_ann(train_sr[pick], dpy))
        e_test, _ = metrics.edge(X[start:stop, pick], b[start:stop])
        folds.append({"start": str(days[start].date()), "end": str(days[stop - 1].date()), "pick": pick,
                      "params": grid[pick], "is_sharpe": is_sr[-1], "oos_sharpe": _ann(stats.sharpe(e_test), dpy)})
    if oos_parts:
        oos_e, _ = metrics.edge(np.concatenate(oos_parts), np.concatenate(oos_bench))
        oos_sr = _ann(stats.sharpe(oos_e), dpy)
    else:
        oos_sr = float("nan")
    mean_is = float(np.nanmean(is_sr)) if is_sr else float("nan")
    ratio = oos_sr / mean_is if math.isfinite(mean_is) and mean_is > 0 else float("nan")
    values.update(wf_oos=oos_sr, wf_ratio=ratio)
    stages.append(Stage(
        "walk_forward", "Walk-forward", None, oos_sr,
        f"OOS edge Sharpe > {th['wf_min_oos_sharpe']}"
        + (f" and >= {th['wf_min_ratio']:.0%} of in-sample" if th["wf_min_ratio"] > 0 else ""),
        f"{len(folds)} yearly folds re-choosing among {len(grid)} settings: out-of-sample edge Sharpe {_f(oos_sr)} "
        f"vs {_f(mean_is)} in-sample ({_f(ratio, '{:.0%}')} kept).",
        {"folds": folds, "oos_sharpe": oos_sr, "is_sharpe": mean_is, "ratio": ratio},
    ))
    lap("walk_forward")

    # 4. Plateau: settings one step away should work too; a lone peak is a fluke.
    names = list(strat.params)
    steps = {k: strat.params[k].step for k in names}
    neighbours = [i for i, cfg in enumerate(grid) if i > 0
                  and all(abs(cfg[k] - cand_params[k]) <= steps[k] + 1e-9 for k in names)]
    floor = max(0.0, 0.5 * cand_edge) if math.isfinite(cand_edge) else 0.0
    good = [i for i in neighbours if _ann(edge_sr[i], dpy) >= floor]
    share = len(good) / len(neighbours) if neighbours else float("nan")
    heat = None
    if names:
        a = names[0]
        y = names[1] if len(names) > 1 else None
        cells = [[cfg[a], cfg[y] if y else 0, _ann(edge_sr[i], dpy)] for i, cfg in enumerate(grid)
                 if all(cfg[k] == cand_params[k] for k in names[2:])]
        heat = {"x": a, "y": y, "cells": cells, "candidate": [cand_params[a], cand_params[y] if y else 0],
                "floor": floor}
    values["plateau"] = share
    stages.append(Stage(
        "plateau", "Plateau", None, share,
        f"share >= {th['plateau_min']}",
        f"{len(good)} of {len(neighbours)} settings one step away keep an edge Sharpe of at least {floor:.2f}."
        if neighbours else "No parameters to vary.", heat,
    ))
    lap("plateau")

    # 5. Permutation: the same bars in random order (keeping the volatility path), the same rule and costs.
    # Skill beats shuffled timing.
    rf_res = None if rf is None else rf[mask].reset_index(drop=True)

    def perm_stat(pb: pd.DataFrame) -> float:
        res = run(pb, strat.positions(pb, cand_params), model, rf_res, with_trades=False)
        r_d = to_daily(res.returns.to_numpy(), pb["ts"]).to_numpy()
        b_d = to_daily(res.benchmark.to_numpy(), pb["ts"]).to_numpy()
        e, _ = metrics.edge(r_d - rf_d, b_d - rf_d)
        return _ann(stats.sharpe(e), dpy)

    real = perm_stat(research_bars)
    n_perm = int(th["n_perm"])
    draw = permuter(research_bars)
    null = np.array([perm_stat(draw(rng)) for _ in range(n_perm)])
    null = np.where(np.isfinite(null), null, 0.0)
    beat = int(np.sum(null >= real)) if math.isfinite(real) else n_perm
    p_value = (1 + beat) / (1 + n_perm)
    values["perm_p"] = p_value
    stages.append(Stage(
        "permutation", "Permutation", None, p_value, f"p <= {th['perm_max_p']}",
        f"Edge Sharpe {_f(real)} on the real history; {beat} of {n_perm} shuffled histories did as well "
        f"(p = {p_value:.3f}).",
        {"null": np.round(null, 4).tolist(), "real": real},
    ))
    lap("permutation")

    # 6. Bootstrap: how deep could drawdowns get, and what size keeps them within budget. Informs sizing only.
    boot = stationary_bootstrap(R[:, 0], int(th["n_boot"]), float(th["boot_block"]), seed, dpy,
                                float(th["dd_budget"]))
    stages.append(Stage(
        "bootstrap", "Bootstrap risk", None, boot["size_for_budget"],
        f"sizing only: 95th-percentile drawdown within {th['dd_budget']:.0%}",
        f"At full size the 95th-percentile drawdown is {boot['max_dd']['p95']:.0%} (median CAGR "
        f"{boot['cagr']['p50']:+.1%}); {boot['size_for_budget']:.0%} of capital keeps it within "
        f"{th['dd_budget']:.0%}.", boot,
    ))
    lap("bootstrap")

    # 7. Selection: the luck threshold after every trial on this instrument, overfitting of the parameter
    # choice, and the chance this is a false discovery.
    n_trials = max(len(trial_sr), 1)
    var_daily = float(np.var(trial_sr, ddof=1)) / dpy if len(trial_sr) > 1 else 0.0
    d = stats.dsr(cand_e, n_trials, var_daily)
    p = pbo(np.nan_to_num(E)) if len(grid) > 1 else {"pbo": float("nan"), "logits": [], "is_sharpe": [],
                                                      "oos_sharpe": [], "n_splits": 0}
    fdr = stats.false_discovery(d["sr"], th["fdr_sr1"] / math.sqrt(dpy), d["n"], th["fdr_prior"], d["skew"],
                                d["kurt"], d["rho"])
    mintrl = stats.min_track_record(d["sr"], 0.0, d["skew"], d["kurt"], d["rho"]) / dpy
    values.update(dsr=d["dsr"], pbo=p["pbo"], fdr=fdr)
    stages.append(Stage(
        "significance", "Deflated Sharpe & PBO", None, d["dsr"],
        f"DSR >= {th['dsr_min']}" + (f" and PBO <= {th['pbo_max']}" if th["pbo_max"] < 1 else " (PBO reported)"),
        f"DSR {_f(d['dsr'], '{:.3f}')} after N = {n_trials} trials on this instrument (luck alone reaches an edge "
        f"Sharpe of {_f(_ann(d['sr0'], dpy))}); PBO {_f(p['pbo'])}; chance it's a false discovery "
        f"{_f(fdr, '{:.0%}')}.",
        {**d, "n_trials": n_trials, "sr_annual": _ann(d["sr"], dpy), "sr0_annual": _ann(d["sr0"], dpy),
         "trial_sharpes": np.round(trial_sr, 4).tolist()[:5000], "pbo": p["pbo"], "logits": p["logits"],
         "is_sharpe": p["is_sharpe"], "oos_sharpe": p["oos_sharpe"], "fdr": fdr, "fdr_prior": th["fdr_prior"],
         "fdr_sr1": th["fdr_sr1"], "min_track_record_years": mintrl},
    ))
    lap("significance")

    # 8. Regimes: up and down markets, calm and turbulent ones, and the worst crashes.
    s_r = pd.Series(R[:, 0], index=days)
    b_r = pd.Series(bench, index=days)
    level = (1.0 + b_r).cumprod()
    sma = level.rolling(200, min_periods=200).mean()
    bull = (level > sma).shift(1, fill_value=False) & sma.shift(1).notna()
    bear = (level <= sma).shift(1, fill_value=False) & sma.shift(1).notna()
    vol = b_r.rolling(63, min_periods=63).std().shift(1)
    calm, turbulent = vol <= vol.median(), vol > vol.median()
    rows = []
    for name, rmask in {"bull": bull, "bear": bear, "calm": calm, "turbulent": turbulent}.items():
        rmask = rmask.fillna(False).astype(bool)
        if rmask.sum() < 20:
            continue
        rows.append({"regime": name, "days": int(rmask.sum()), "strategy": float(s_r[rmask].mean() * dpy),
                     "benchmark": float(b_r[rmask].mean() * dpy)})
    crises = [{"regime": f"crash {pk.date()} → {tr.date()}", "days": int(((days >= pk) & (days <= tr)).sum()),
               "strategy": float((1 + s_r[pk:tr]).prod() - 1), "benchmark": float((1 + b_r[pk:tr]).prod() - 1),
               "depth": depth} for pk, tr, depth in crisis_windows(b_r)]
    positive = sum(r["strategy"] > 0 for r in rows)
    share_pos = positive / len(rows) if rows else float("nan")
    values["regime_share"] = share_pos
    stages.append(Stage(
        "regimes", "Regimes", None, share_pos,
        f"positive in >= {th['regime_min_share']:.0%} of regimes",
        f"Net return positive in {positive} of {len(rows)} regimes. "
        + "; ".join(f"{c['regime'].split(' ')[1]} crash: {c['strategy']:+.0%} vs {c['benchmark']:+.0%}"
                    for c in crises),
        {"rows": rows, "crises": crises},
    ))
    lap("regimes")

    # 9. Diversification against strategies that are already validated.
    joined = None
    values.update(div_corr=float("nan"), div_gain=float("nan"))
    if portfolio is not None:
        joined = pd.concat([s_r.rename("c"), portfolio.rename("p")], axis=1, join="inner").dropna()
    if joined is not None and len(joined) >= dpy:
        corr = float(joined["c"].corr(joined["p"]))
        mix = 0.5 * joined["c"] + 0.5 * joined["p"]
        sr_p, sr_mix = _ann(stats.sharpe(joined["p"]), dpy), _ann(stats.sharpe(mix), dpy)
        values.update(div_corr=corr, div_gain=sr_mix - sr_p)
        stages.append(Stage(
            "diversification", "Diversification", None, corr,
            f"correlation <= {th['max_corr']} and the mix's Sharpe >= the portfolio's",
            f"Correlation {corr:.2f} with the validated portfolio; a 50/50 mix has Sharpe {sr_mix:.2f} vs "
            f"{sr_p:.2f} alone.", {"corr": corr, "sharpe_portfolio": sr_p, "sharpe_mix": sr_mix},
        ))
    else:
        corr_b = float(np.corrcoef(R[:, 0], bench)[0, 1]) if np.std(R[:, 0]) > 0 else float("nan")
        stages.append(Stage(
            "diversification", "Diversification", None, corr_b,
            f"correlation to the validated portfolio <= {th['max_corr']}",
            f"No validated strategies overlap enough to compare. Correlation to buy-and-hold: {_f(corr_b)}.",
            {"corr_benchmark": corr_b},
        ))
    lap("diversification")

    # 10. Lockbox: sealed until opened, once per question.
    stages.append(Stage(
        "lockbox", "Lockbox", None, None, "edge Sharpe inside the one-sided 95% band of the research result",
        f"Sealed: {sealed_from.date()} onward. Opened once per research question.",
    ))

    verdicts = judge(values, th)
    for st in stages:
        st.passed = verdicts[st.key]
    failed = next((s.key for s in stages if s.passed is False), None)
    return {"stages": stages, "failed_stage": failed, "values": values, "research": research, "grid": grid,
            "grid_size": len(grid), "records": records, "n_trials": n_trials, "timings": timings,
            "candidate_daily": s_r, "candidate_edge_daily": pd.Series(cand_e, index=days)}


def validated_portfolio(exclude_question: str | None = None) -> pd.Series | None:
    """Equal-weight daily returns of every validated strategy, the portfolio a new candidate must diversify.

    Until M6 brings a real allocator, validated strategies are combined with equal weights on the dates they
    share.

    Args:
        exclude_question: Leave out cards on this research question (the candidate's own).

    Returns:
        Daily returns indexed by date, or None when nothing is validated.
    """
    cards = [c for c in list_cards(500) if c["verdict"] == "validated" and c["question"] != exclude_question]
    series = []
    for c in cards:
        full = load_card(c["card_id"])
        m = full["meta"]
        try:
            bars = runner.load_bars(m["source"], m["symbol"], m["interval"])
        except FileNotFoundError:
            continue
        strat = REGISTRY.get(m["strategy"])
        if strat is None:
            continue
        rf = runner.risk_free(m["source"], bars, m["interval"])
        res = run(bars, strat.positions(bars, m["params"]), runner.resolve_costs(m["costs"], m["source"]), rf,
                  with_trades=False)
        series.append(to_daily(res.returns.to_numpy(), bars["ts"]).rename(c["card_id"]))
    if not series:
        return None
    return pd.concat(series, axis=1).mean(axis=1, skipna=True)


def run_gauntlet(
    source: str,
    symbol: str,
    interval: str,
    strategy: str,
    params: dict[str, float] | None = None,
    costs: str | dict[str, float] = "auto",
    seed: int = 7,
    actor: str = "user",
) -> dict[str, Any]:
    """Judge a candidate strategy, record every grid setting as a trial, and save the report card.

    Args:
        source: Data source.
        symbol: Instrument.
        interval: Bar interval.
        strategy: Registry key.
        params: Candidate parameters (defaults filled in).
        costs: Cost preset, dict or ``"auto"``.
        seed: Seed for the grid fill, permutations and bootstrap, for reproducibility.
        actor: Who ran it, for the audit log.

    Returns:
        The report card: ``card_id``, ``verdict`` (``candidate`` or ``rejected``), ``failed_stage``, the
        ``stages`` list, ``meta`` and the ``thresholds`` used.

    Raises:
        KeyError: Unknown strategy.
        FileNotFoundError: The series isn't ingested.
        ValueError: Too little research data before the lockbox.
    """
    version, th = thresholds.current()
    strat = REGISTRY[strategy]
    cand_params = {**strat.defaults(), **(params or {})}
    cand_params = {k: (int(v) if strat.params[k].is_int else float(v)) for k, v in cand_params.items()}
    bars = runner.load_bars(source, symbol, interval)
    model = runner.resolve_costs(costs, source)
    rf = runner.risk_free(source, bars, interval)
    sealed = lockbox.sealed_from()
    question = f"{strat.family}:{symbol}:{interval}"
    research = bars[bars["ts"] < sealed]
    research_hash = store.data_hash(research)
    period = (str(research["ts"].iloc[0].date()), str(research["ts"].iloc[-1].date())) if len(research) else ("", "")
    prior = ledger.selection_set(symbol, interval)
    cand_key = (strategy, db.dumps(cand_params), *period)
    counted = any((t["strategy"], t["params"], t["start"], t["end"]) == cand_key for t in prior)

    started = time.perf_counter()
    out = evaluate(bars, strat, cand_params, model, rf, dpy=days_per_year(source), sealed_from=sealed, th=th,
                   selection=[t["edge_sharpe"] for t in prior], candidate_counted=counted,
                   portfolio=validated_portfolio(question), seed=seed)
    ledger.record_trials([
        {**r, "question": question, "family": strat.family, "strategy": strategy, "symbol": symbol,
         "source": "gauntlet", "data_hash": research_hash} for r in out["records"]
    ])
    stages = out["stages"]
    spent = lockbox.spent(question)
    if spent:
        stages[-1].summary = ("Spent: a trial on this question has already looked at data from "
                              f"{sealed.date()} on, so the lockbox can't give an independent verdict. A forward "
                              "paper test (M7) has to take its place.")
    verdict = "rejected" if out["failed_stage"] else "candidate"
    card_id = uuid.uuid4().hex[:12]
    card = {
        "card_id": card_id,
        "created_at": naive_utc().isoformat(timespec="seconds"),
        "verdict": verdict,
        "failed_stage": out["failed_stage"],
        "meta": {"question": question, "family": strat.family, "strategy": strategy, "params": cand_params,
                 "source": source, "symbol": symbol, "interval": interval, "costs": model.__dict__,
                 "research": out["research"], "lockbox_from": str(sealed.date()), "lockbox_spent": spent,
                 "grid_size": out["grid_size"], "n_trials": out["n_trials"], "seed": seed,
                 "data_hash": research_hash, "version": code_version(),
                 "elapsed_s": round(time.perf_counter() - started, 2), "timings": out["timings"]},
        "thresholds": {"version": version, **th},
        "stages": [asdict(s) for s in stages],
    }
    card = clean(card)
    _save_card(card)
    audit.log("gauntlet.run", target=card_id, actor=actor,
              details={"question": question, "verdict": verdict, "failed_stage": out["failed_stage"]})
    return card


def _save_card(card: dict[str, Any]) -> None:
    """Write a report card to disk and upsert its catalog row.

    Args:
        card: Report card as returned by ``run_gauntlet``.
    """
    path = config.reports_dir() / "gauntlet" / f"{card['card_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(card), default=_json_default))
    m = card["meta"]
    with db.connect() as con:
        con.execute(
            """INSERT OR REPLACE INTO gauntlet_cards
               (card_id, created_at, question, family, strategy, params, symbol, verdict, stages,
                thresholds_version, report_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [card["card_id"], card["created_at"], m["question"], m["family"], m["strategy"], db.dumps(m["params"]),
             m["symbol"], card["verdict"],
             db.dumps([{k: s[k] for k in ("key", "passed", "value")} for s in card["stages"]]),
             card["thresholds"]["version"], str(path)],
        )


def clean(obj: Any) -> Any:
    """Make a value strict-JSON safe: NaN and infinity become None, numpy scalars become Python numbers.

    Browsers reject NaN in JSON, and missing values are common in report cards (no PBO without parameters).

    Args:
        obj: Nested dicts, lists, tuples and scalars.

    Returns:
        The same structure with only JSON-native values.
    """
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, float | np.floating):
        return float(obj) if math.isfinite(obj) else None
    return obj


def _json_default(obj: Any) -> Any:
    """JSON fallback for numpy scalars and timestamps.

    Args:
        obj: Value the json module can't serialise.

    Returns:
        A plain Python value.
    """
    if isinstance(obj, np.integer | np.floating):
        return obj.item()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return str(obj)


def load_card(card_id: str) -> dict[str, Any]:
    """Load a saved report card.

    Args:
        card_id: Card id.

    Returns:
        The card.

    Raises:
        FileNotFoundError: Unknown card.
    """
    path = config.reports_dir() / "gauntlet" / f"{card_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no report card {card_id}")
    return json.loads(path.read_text())


def list_cards(limit: int = 200) -> list[dict[str, Any]]:
    """Recent report cards, newest first.

    Args:
        limit: Maximum number of cards.

    Returns:
        Catalog rows with ``params`` and ``stages`` decoded.
    """
    rows = db.rows("SELECT * FROM gauntlet_cards ORDER BY created_at DESC LIMIT ?", [limit])
    for r in rows:
        r["params"] = json.loads(r["params"] or "{}")
        r["stages"] = json.loads(r["stages"] or "[]")
    return rows


def funnel() -> list[dict[str, Any]]:
    """How many report cards reached and passed each stage.

    A card reaches a stage when no earlier stage failed. Cards that failed early still ran every stage, but
    the funnel counts them only up to their first failure, the way a candidate would be stopped.

    Returns:
        One row per stage: ``key, name, reached, passed, failed``.
    """
    cards = list_cards(100_000)
    out = []
    for key, name in STAGES:
        reached = passed = failed = 0
        for c in cards:
            by_key = {s["key"]: s for s in c["stages"]}
            order = [k for k, _ in STAGES]
            earlier = order[: order.index(key)]
            if any(by_key.get(k, {}).get("passed") is False for k in earlier):
                continue
            reached += 1
            state = by_key.get(key, {}).get("passed")
            passed += state is True
            failed += state is False
        out.append({"key": key, "name": name, "reached": reached, "passed": passed, "failed": failed})
    return out


def open_lockbox(card_id: str, confirm: str, reason: str, actor: str = "user") -> dict[str, Any]:
    """Judge a candidate on the sealed lockbox period. Allowed once per research question.

    The lockbox passes when the edge Sharpe on sealed data is no worse than the research edge Sharpe minus
    1.645 standard errors: a true edge passes 95% of the time, while one that only existed in the research
    data is likely to fall outside. With two years of data the band is wide, so this catches large decay, not
    small; the forward paper test (M7) carries on from here.

    Args:
        card_id: Card of the candidate.
        confirm: Must equal the research question, typed by the person (friction on purpose).
        reason: Why now, in plain words.
        actor: Who opened it.

    Returns:
        The updated card, with verdict ``validated`` or ``rejected``.

    Raises:
        FileNotFoundError: Unknown card.
        ValueError: Wrong confirmation, missing reason, a card that didn't pass stages 1-9, or a lockbox that
            is already spent.
    """
    card = load_card(card_id)
    m = card["meta"]
    if confirm.strip() != m["question"]:
        raise ValueError(f"type the research question ({m['question']}) to confirm")
    if not reason.strip():
        raise ValueError("a reason is required to open the lockbox")
    if card["verdict"] != "candidate":
        raise ValueError("only cards that passed stages 1-9 can open the lockbox")
    if lockbox.spent(m["question"]):
        raise ValueError("this question's lockbox is already spent; a forward paper test has to take its place")

    strat = REGISTRY[m["strategy"]]
    bars = runner.load_bars(m["source"], m["symbol"], m["interval"])
    model = runner.resolve_costs(m["costs"], m["source"])
    rf = runner.risk_free(m["source"], bars, m["interval"])
    res = run(bars, strat.positions(bars, m["params"]), model, rf, with_trades=False)
    sealed = pd.Timestamp(m["lockbox_from"], tz="UTC")
    lock = (bars["ts"] >= sealed).to_numpy()
    dpy = days_per_year(m["source"])
    ts_l = bars["ts"][lock]
    daily = to_daily(res.returns.to_numpy()[lock], ts_l)
    bench = to_daily(res.benchmark.to_numpy()[lock], ts_l).to_numpy()
    rf_d = np.zeros(len(daily)) if rf is None else to_daily(rf.to_numpy()[lock], ts_l).to_numpy()
    e, _ = metrics.edge(daily.to_numpy(), bench, rf_d)
    sr_lock = stats.sharpe(e)

    sig = next(s for s in card["stages"] if s["key"] == "significance")["chart"]
    sr_research = sig["sr"]
    se = math.sqrt(stats.sr_variance(sr_research, len(daily), sig["skew"], sig["kurt"], sig["rho"]))
    floor = sr_research - Z_95 * se
    passed = bool(math.isfinite(sr_lock) and sr_lock >= floor)

    stats_lock = metrics.summary(daily, pd.Series(rf_d, index=daily.index), dpy,
                                 benchmark=pd.Series(bench, index=daily.index))
    ledger.record_trial(question=m["question"], family=m["family"], strategy=m["strategy"], params=m["params"],
                        symbol=m["symbol"], start=str(ts_l.iloc[0].date()), end=str(ts_l.iloc[-1].date()),
                        source="lockbox", metrics=stats_lock, data_hash=store.data_hash(bars[lock]))
    lockbox.record_opening(m["family"], m["question"], card_id, reason)

    stage = next(s for s in card["stages"] if s["key"] == "lockbox")
    stage.update({
        "passed": passed,
        "value": _ann(sr_lock, dpy),
        "summary": f"Edge Sharpe {_f(_ann(sr_lock, dpy))} over {len(daily)} sealed days vs "
                   f"{_f(_ann(sr_research, dpy))} in research (band floor {_f(_ann(floor, dpy))}); "
                   f"net CAGR {_f(stats_lock['cagr'], '{:+.1%}')}, max drawdown {_f(stats_lock['max_dd'], '{:.0%}')}.",
        "chart": {"edge_sharpe": _ann(sr_lock, dpy), "floor": _ann(floor, dpy), "research": _ann(sr_research, dpy),
                  "cagr": stats_lock["cagr"], "max_dd": stats_lock["max_dd"], "days": len(daily)},
    })
    card["verdict"] = "validated" if passed else "rejected"
    card["failed_stage"] = None if passed else "lockbox"
    card["meta"]["lockbox_spent"] = True
    card = clean(card)
    _save_card(card)
    audit.log("gauntlet.lockbox", target=card_id, reason=reason, actor=actor,
              details={"question": m["question"], "passed": passed, "edge_sharpe": _ann(sr_lock, dpy)})
    return card
