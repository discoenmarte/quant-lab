"""Score mined specs on research data: one backtest, a look-ahead check and the numbers the ledger records.

Design:

- **Research data only.** ``Evaluator.for_scope`` loads bars or a panel up to the day before the lockbox
  (``lockbox.research_end``); for a universe it also drops instruments that are never members before then. The
  miner never reads a sealed bar. When a mined candidate later goes to the gauntlet, the gauntlet computes
  positions on the full history and slices them, as for every card; stage 1 checks that changes nothing.
- **The same numbers as the gauntlet.** The objective is the edge Sharpe after costs (``metrics.summary``'s
  ``edge_sharpe``, annual): the return beyond a static position in the benchmark (decision 0004 §1). Costs and
  turnover are paid inside it, so the search can't win by trading more.
- **A look-ahead check on every trial** (``leak_cuts`` future perturbations, two by default; the gauntlet runs
  five on the candidate it judges). The grammar can't express look-ahead, so a failure is a bug: the trial is
  still recorded, flagged, and never ranked.
- **Workers.** ``evaluate_many`` spreads specs over processes; each worker loads the scope once.
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import lockbox
from ..backtest import metrics, runner
from ..backtest.engine import COST_PRESETS, CostModel, run
from ..backtest.leakage import future_shift_check
from ..data import store
from ..data.ingest import SOURCES
from ..portfolio import leakage as panel_leakage
from ..portfolio.engine import run_portfolio
from ..portfolio.panel import Panel, load_panel
from ..portfolio.runner import clip_to, panel_hash
from . import grammar
from .grammar import Space

INVALID = -10.0  # objective of a spec that couldn't be scored or leaked; below any real edge Sharpe


@dataclass(frozen=True)
class Scope:
    """What a mining job searches on.

    Attributes:
        kind: ``bars`` (one instrument) or ``panel`` (a universe).
        name: The instrument (``SPY``) or universe (``crypto_top30``).
        source: Data source of the instrument (bars only).
        interval: Bar interval; mining uses daily bars.
        costs: Cost preset for one instrument (``auto`` by asset class).
    """

    kind: str
    name: str
    source: str = ""
    interval: str = "1d"
    costs: str = "auto"

    @property
    def question(self) -> str:
        """The mining question: every mined trial on this scope, ``mined:<scope>:<interval>``."""
        return f"{grammar.FAMILY}:{self.name}:{self.interval}"

    def to_dict(self) -> dict[str, Any]:
        """The scope as JSON-ready data."""
        return {"kind": self.kind, "name": self.name, "source": self.source, "interval": self.interval,
                "costs": self.costs}


@dataclass
class Result:
    """One scored spec.

    Attributes:
        spec: The normalised spec.
        name: Strategy name (``mined_<hash>``).
        params: Its tunable parameters.
        edge: Annual edge Sharpe after costs; ``INVALID`` when it couldn't be scored or leaked.
        gross_edge: Annual edge Sharpe before costs.
        metrics: ``metrics.summary`` of the daily returns, plus ``gross_edge_sharpe``, ``turnover``, ``nodes`` and
            ``leak``: what the ledger records.
        leak: Whether the look-ahead check failed.
        error: Why it couldn't be scored, or "".
        daily_edge: Daily edge returns (float32), for originality and de-duplication; None when not kept.
        seconds: Time taken.
    """

    spec: dict[str, Any]
    name: str
    params: dict[str, Any]
    edge: float
    gross_edge: float
    metrics: dict[str, Any]
    leak: bool = False
    error: str = ""
    daily_edge: np.ndarray | None = field(default=None, repr=False)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """Whether the result can be ranked."""
        return not self.leak and not self.error and math.isfinite(self.edge) and self.edge > INVALID


def _annual(x: np.ndarray, dpy: int) -> float:
    """Annual Sharpe of a daily series.

    Args:
        x: Daily returns.
        dpy: Days per year.

    Returns:
        The Sharpe, or NaN.
    """
    return float(metrics.sharpe(x, None, dpy))


class Evaluator:
    """Scores specs on one scope's research data.

    Attributes:
        space: The grammar space of the scope.
        scope: The scope (None for synthetic markets).
        kind: ``bars`` or ``panel``.
        dpy: Days per year.
        research: First and last research date.
        data_hash: Fingerprint of the data.
        leak_cuts: Future perturbations per trial.
    """

    def __init__(self, space: Space, *, bars: pd.DataFrame | None = None, panel: Panel | None = None,
                 model: CostModel | None = None, rf: pd.Series | None = None, scope: Scope | None = None,
                 leak_cuts: int = 2) -> None:
        """Prepare an evaluator from data already loaded and clamped to research.

        Args:
            space: The grammar space.
            bars: Bars, for a single instrument.
            panel: Panel, for a universe.
            model: Cost model (bars).
            rf: Risk-free return per bar (bars); None means zero.
            scope: The real scope, or None for a synthetic market.
            leak_cuts: Future perturbations per trial; 0 skips the check (tests of speed only).
        """
        self.space, self.scope, self.kind, self.leak_cuts = space, scope, space.kind, leak_cuts
        self.bars, self.panel, self.model, self.rf_bars = bars, panel, model, rf
        self.dpy = space.dpy
        if bars is not None:
            days = pd.DatetimeIndex(bars["ts"])
            self.rf = np.zeros(len(bars)) if rf is None else rf.to_numpy(float)
            self.data_hash = store.data_hash(bars)
        else:
            days = panel.ts
            self.rf = panel.rf
            self.data_hash = panel_hash(panel)
        self.research = (str(days[0].date()), str(days[-1].date()))
        self.n_days = len(days)

    @classmethod
    def for_scope(cls, scope: Scope, banned: tuple[str, ...] = (), leak_cuts: int = 2,
                  end: str | None = None) -> Evaluator:
        """Load a real scope's research data (never anything from the lockbox date on).

        Args:
            scope: What to search.
            banned: Parts of the grammar to leave out.
            leak_cuts: Future perturbations per trial.
            end: Last research date; None reads it from the thresholds in force. Worker processes get it passed
                in, so they never open the database while the server holds it.

        Returns:
            The evaluator.

        Raises:
            FileNotFoundError: The data isn't ingested.
            KeyError: Unknown universe or cost preset.
            ValueError: Fewer than three years of research data, or a scope kind other than bars or panel.
        """
        end = end or lockbox.research_end()
        if scope.kind == "bars":
            bars = runner.load_bars(scope.source, scope.name, scope.interval, end=end)
            info = SOURCES.get(scope.source)
            asset = info.asset_class if info else "equity"
            dpy = 365 if asset == "crypto" else 252
            space = grammar.bars_space(asset, dpy, banned)
            ev = cls(space, bars=bars, model=runner.resolve_costs(scope.costs, scope.source),
                     rf=runner.risk_free(scope.source, bars, scope.interval), scope=scope, leak_cuts=leak_cuts)
        elif scope.kind == "panel":
            panel = research_panel(load_panel(scope.name), end)
            ev = cls(grammar.panel_space(panel, banned), panel=panel, scope=scope, leak_cuts=leak_cuts)
        else:
            raise ValueError(f"unknown scope kind {scope.kind!r}")
        if ev.n_days < 3 * ev.dpy:
            raise ValueError("need at least three years of research data before the lockbox")
        return ev

    @classmethod
    def synthetic(cls, *, bars: pd.DataFrame | None = None, panel: Panel | None = None, leak_cuts: int = 2,
                  banned: tuple[str, ...] = ()) -> Evaluator:
        """An evaluator on a synthetic market, for calibration: ETF costs and no lockbox.

        Args:
            bars: Synthetic bars (an equity-like market).
            panel: A synthetic panel.
            leak_cuts: Future perturbations per trial.
            banned: Parts of the grammar to leave out.

        Returns:
            The evaluator.
        """
        if bars is not None:
            return cls(grammar.bars_space("equity", 252, banned), bars=bars, model=COST_PRESETS["etf"],
                       leak_cuts=leak_cuts)
        return cls(grammar.panel_space(panel, banned), panel=panel, leak_cuts=leak_cuts)

    def evaluate(self, spec: dict[str, Any], keep_daily: bool = True, seed: int = 7) -> Result:
        """Backtest one spec on the research data and check it for look-ahead.

        Args:
            spec: A normalised spec of this space.
            keep_daily: Return the daily edge series too.
            seed: Seed of the look-ahead check's cuts.

        Returns:
            The ``Result``; a spec that fails to run comes back with ``error`` set and ``INVALID`` edge.
        """
        t0 = time.perf_counter()
        name, _, params = grammar.split(spec, self.space)
        try:
            strat = grammar.compile_spec(spec, self.space)
            if self.kind == "bars":
                res = run(self.bars, strat.positions(self.bars), self.model, self.rf_bars, with_trades=False,
                          session=strat.session)
                net, gross = res.returns.to_numpy(), (res.gross + res.cash).to_numpy()
                bench = res.benchmark.to_numpy()
                per_unit = self.model.per_unit
                traded = float(res.costs.sum()) / per_unit if per_unit > 0 else float(
                    np.abs(np.diff(res.position.to_numpy(), prepend=0.0)).sum())
                exposure = float(np.mean(np.abs(res.position.to_numpy()) > 1e-9))
            else:
                pres = run_portfolio(self.panel, strat.weights(self.panel))
                net, gross, bench = pres.net, pres.gross + pres.cash, pres.benchmark
                traded = float(pres.turnover.sum())
                exposure = float(np.mean(np.abs(pres.held).sum(axis=1) > 1e-9))
            years = max(len(net) / self.dpy, 1e-9)
            stats = metrics.summary(pd.Series(net), pd.Series(self.rf), self.dpy, benchmark=pd.Series(bench))
            e, _ = metrics.edge(net, bench, self.rf)
            g, _ = metrics.edge(gross, bench, self.rf)
            gross_edge = _annual(g, self.dpy)
            leak = False
            if self.leak_cuts:
                check = (future_shift_check(strat, self.bars, None, n_cuts=self.leak_cuts, seed=seed)
                         if self.kind == "bars" else
                         panel_leakage.future_shift_check(strat, self.panel, None, n_cuts=self.leak_cuts, seed=seed))
                leak = not check["passed"]
            sig = grammar.signature(spec)
            stats.update(gross_edge_sharpe=gross_edge, turnover=traded / years, exposure=exposure,
                         nodes=sig["nodes"], leak=leak)
            edge = float(stats.get("edge_sharpe", float("nan")))
            ok_edge = edge if math.isfinite(edge) and not leak else INVALID
            return Result(spec, name, params, ok_edge, gross_edge, stats, leak=leak,
                          daily_edge=e.astype(np.float32) if keep_daily else None,
                          seconds=time.perf_counter() - t0)
        except (ValueError, KeyError, IndexError, FloatingPointError, ZeroDivisionError) as err:
            return Result(spec, name, params, INVALID, float("nan"), {"n_obs": 0, "error": str(err)[:200]},
                          error=str(err)[:200], seconds=time.perf_counter() - t0)


def research_panel(panel: Panel, end: str | None = None) -> Panel:
    """A panel cut to the research period, without instruments that are never members before the lockbox.

    Membership is point in time, so cutting after loading changes no member. Dropping instruments that only
    become members later keeps the miner from seeing which coins exist after the lockbox.

    Args:
        panel: The full panel.
        end: Last research date; None reads it from the thresholds in force.

    Returns:
        The research panel.
    """
    cut = clip_to(panel, None, end or lockbox.research_end())
    keep = np.flatnonzero(cut.member.any(axis=0))
    if len(keep) == cut.n:
        return cut
    return Panel(cut.name, cut.ts, [cut.symbols[i] for i in keep], [cut.sources[i] for i in keep],
                 [cut.asset_class[i] for i in keep], cut.open[:, keep], cut.close[:, keep], cut.rf, cut.costs[keep],
                 cut.avail[:, keep], cut.member[:, keep], cut.dollar_volume[:, keep], cut.rule, cut.drift)


# ---------------------------------------------------------------------------------------------------------------
# Parallel evaluation

_WORKER: Evaluator | None = None


def _init_worker(scope: dict[str, Any] | None, banned: tuple[str, ...], leak_cuts: int,
                 synthetic: dict[str, Any] | None, end: str | None = None) -> None:
    """Load the scope once in a worker process, without opening the database.

    Args:
        scope: ``Scope.to_dict()``, or None for a synthetic market.
        banned: Parts of the grammar to leave out.
        leak_cuts: Future perturbations per trial.
        synthetic: Arguments of ``Evaluator.synthetic`` (bars or panel), when ``scope`` is None.
        end: Last research date of a real scope.
    """
    global _WORKER
    if scope is not None:
        _WORKER = Evaluator.for_scope(Scope(**scope), banned, leak_cuts, end)
    else:
        _WORKER = Evaluator.synthetic(**synthetic, leak_cuts=leak_cuts, banned=banned)


def _work(args: tuple[dict[str, Any], int]) -> Result:
    """Score one spec in a worker.

    Args:
        args: ``(spec, seed)``.

    Returns:
        The result.
    """
    spec, seed = args
    return _WORKER.evaluate(spec, seed=seed)


class Pool:
    """Scores batches of specs in worker processes, each holding the scope's data.

    Attributes:
        workers: Number of processes.
    """

    def __init__(self, evaluator: Evaluator, workers: int) -> None:
        """Start the workers.

        Args:
            evaluator: The in-process evaluator (used directly when ``workers`` is 1).
            workers: Processes; 1 evaluates in this process.
        """
        self.evaluator, self.workers = evaluator, max(1, workers)
        self._pool = None
        if self.workers > 1:
            ev = evaluator
            synthetic = None
            if ev.scope is None:
                synthetic = {"bars": ev.bars} if ev.kind == "bars" else {"panel": ev.panel}
            self._pool = ProcessPoolExecutor(
                self.workers, initializer=_init_worker,
                initargs=(ev.scope.to_dict() if ev.scope else None, ev.space.banned, ev.leak_cuts, synthetic,
                          ev.research[1] if ev.scope else None))

    def evaluate(self, specs: list[dict[str, Any]], seed: int = 7) -> list[Result]:
        """Score specs, in order.

        Args:
            specs: Normalised specs.
            seed: Base seed of the look-ahead checks.

        Returns:
            One result per spec.
        """
        jobs = [(s, seed + i) for i, s in enumerate(specs)]
        if self._pool is None:
            return [self.evaluator.evaluate(s, seed=k) for s, k in jobs]
        return list(self._pool.map(_work, jobs, chunksize=max(1, len(jobs) // (4 * self.workers))))

    def close(self) -> None:
        """Stop the workers."""
        if self._pool is not None:
            self._pool.shutdown(cancel_futures=True)
            self._pool = None
