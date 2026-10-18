"""What the gauntlet judges: a strategy with a parameter space, on data, with a cost model.

The stages need only a few things from a candidate: daily returns for any parameter setting on the research
period (with gross returns, the benchmark and turnover), a look-ahead check, permutation worlds, and returns on
the sealed lockbox period. A subject supplies them for one kind of strategy, so the stages never see bars or
panels and one gauntlet judges both single-instrument rules and whole portfolios.

- ``BarSubject``: a single-instrument strategy on bars. Benchmark: buy-and-hold of the instrument.
- ``PanelSubject``: a portfolio strategy on a multi-asset panel. Benchmark: equal weight of the same instruments
  through the same engine, the implementable passive alternative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..backtest import runner
from ..backtest.engine import CostModel, run
from ..backtest.leakage import future_shift_check
from ..backtest.strategies import REGISTRY, Param
from ..data import store
from ..data.ingest import SOURCES
from ..portfolio import leakage as panel_leakage
from ..portfolio.engine import run_portfolio
from ..portfolio.panel import Panel, cost_model_of, load_panel
from ..portfolio.runner import panel_hash, question_of
from ..portfolio.strategies import REGISTRY as PORTFOLIOS
from . import stats
from .nulls import local_vol, permuter


@dataclass
class Sim:
    """Daily results of one parameter setting on the research period.

    Attributes:
        net: Net return per day.
        gross: Return before trading costs (price moves plus cash interest).
        bench: Benchmark return per day.
        turnover: Position change per year (1.0 = the whole book once).
    """

    net: np.ndarray
    gross: np.ndarray
    bench: np.ndarray
    turnover: float


def to_daily(returns: np.ndarray | pd.Series, ts: pd.Series | pd.DatetimeIndex) -> pd.Series:
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


def days_per_year(source: str) -> int:
    """Trading days per year for daily statistics.

    Args:
        source: Source name.

    Returns:
        365 for crypto, 252 otherwise.
    """
    return 365 if SOURCES.get(source) and SOURCES[source].asset_class == "crypto" else 252


def _edge_sharpe_annual(net: np.ndarray, bench: np.ndarray, rf: np.ndarray, dpy: int) -> float:
    """Annualised edge Sharpe of a daily return stream.

    Args:
        net: Strategy returns.
        bench: Benchmark returns.
        rf: Risk-free returns.
        dpy: Days per year.

    Returns:
        Annual appraisal ratio, or NaN.
    """
    from ..backtest import metrics

    e, _ = metrics.edge(net - rf, bench - rf)
    sr = stats.sharpe(e)
    return float(sr * np.sqrt(dpy)) if np.isfinite(sr) else float("nan")


class BarSubject:
    """A single-instrument strategy on bars.

    Attributes:
        kind: ``"bars"``.
        strat: The registered strategy.
        params: Its parameter space.
        question, family, strategy, scope, interval: Ledger keys; ``scope`` is the instrument.
        dpy: Days per year.
        days: Research dates.
        rf: Risk-free return per research day.
        research: First and last research date.
        data_hash: Fingerprint of the research data.
        cost_bps: Cost per unit traded, in basis points.
        meta: What the report card records about the subject.
    """

    kind = "bars"

    def __init__(
        self,
        bars: pd.DataFrame,
        strategy: str,
        sealed_from: pd.Timestamp,
        *,
        source: str,
        symbol: str,
        interval: str,
        model: CostModel,
        rf: pd.Series | None,
        dpy: int,
    ) -> None:
        """Prepare a subject from bars already loaded.

        Args:
            bars: Full history of normalised bars.
            strategy: Registry key.
            sealed_from: First lockbox date.
            source: Data source (or ``"synthetic"``).
            symbol: Instrument.
            interval: Bar interval.
            model: Cost model.
            rf: Risk-free return per bar, aligned with ``bars``; None means zero.
            dpy: Days per year.
        """
        self.strat = REGISTRY[strategy]
        self.params: dict[str, Param] = self.strat.params
        self.family, self.strategy, self.scope, self.interval = self.strat.family, strategy, symbol, interval
        self.question = f"{self.family}:{symbol}:{interval}"
        self.bars, self.model, self.rf_bars, self.dpy, self.sealed = bars, model, rf, dpy, sealed_from
        self.mask = (bars["ts"] < sealed_from).to_numpy()
        self.ts_r = bars["ts"][self.mask]
        self.days = to_daily(np.zeros(int(self.mask.sum())), self.ts_r).index
        self.rf = np.zeros(len(self.days)) if rf is None else to_daily(rf.to_numpy()[self.mask], self.ts_r).to_numpy()
        self.research_bars = bars[self.mask].reset_index(drop=True)
        self.research = (str(self.days[0].date()), str(self.days[-1].date())) if len(self.days) else ("", "")
        self.data_hash = store.data_hash(self.research_bars)
        self.cost_bps = model.per_unit * 1e4
        self.meta: dict[str, Any] = {"kind": "bars", "source": source, "symbol": symbol, "interval": interval,
                                     "costs": model.__dict__}

    @classmethod
    def load(cls, source: str, symbol: str, interval: str, strategy: str, sealed_from: pd.Timestamp,
             costs: str | dict[str, float] | CostModel = "auto") -> BarSubject:
        """Load stored bars and build the subject.

        Args:
            source: Data source.
            symbol: Instrument.
            interval: Bar interval.
            strategy: Registry key.
            sealed_from: First lockbox date.
            costs: Cost preset, dict or model.

        Returns:
            The subject.

        Raises:
            KeyError: Unknown strategy or cost preset.
            FileNotFoundError: The series isn't ingested.
        """
        bars = runner.load_bars(source, symbol, interval)
        return cls(bars, strategy, sealed_from, source=source, symbol=symbol, interval=interval,
                   model=runner.resolve_costs(costs, source), rf=runner.risk_free(source, bars, interval),
                   dpy=days_per_year(source))

    def simulate(self, cfg: dict[str, float]) -> Sim:
        """Daily results of one setting on the research period, positions computed on the full history.

        Args:
            cfg: Parameters.

        Returns:
            A ``Sim``.
        """
        res = run(self.bars, self.strat.positions(self.bars, cfg), self.model, self.rf_bars, with_trades=False)
        m = self.mask
        pos = res.position.to_numpy()[m]
        return Sim(
            net=to_daily(res.returns.to_numpy()[m], self.ts_r).to_numpy(),
            gross=to_daily((res.gross + res.cash).to_numpy()[m], self.ts_r).to_numpy(),
            bench=to_daily(res.benchmark.to_numpy()[m], self.ts_r).to_numpy(),
            turnover=float(np.abs(np.diff(pos, prepend=0.0)).sum()) / (len(self.days) / self.dpy),
        )

    def leak_check(self, cfg: dict[str, float], seed: int) -> dict[str, Any]:
        """Future-shift check on the research bars.

        Args:
            cfg: Parameters.
            seed: Random seed.

        Returns:
            ``passed``, ``cuts`` and ``first_violation``.
        """
        return future_shift_check(self.strat, self.research_bars, cfg, seed=seed)

    def permutation(self, cfg: dict[str, float], n: int, rng: np.random.Generator) -> tuple[float, np.ndarray]:
        """Edge Sharpe on the real research history and on ``n`` volatility-preserving shuffles of it.

        Positions are computed on the research data alone in both, so warm-up is treated the same way.

        Args:
            cfg: Parameters.
            n: Number of shuffles.
            rng: Random generator.

        Returns:
            ``(real, null)`` annual edge Sharpes.
        """
        rf_bars = None if self.rf_bars is None else self.rf_bars[self.mask].reset_index(drop=True)

        def stat(b: pd.DataFrame) -> float:
            res = run(b, self.strat.positions(b, cfg), self.model, rf_bars, with_trades=False)
            net = to_daily(res.returns.to_numpy(), b["ts"]).to_numpy()
            bench = to_daily(res.benchmark.to_numpy(), b["ts"]).to_numpy()
            return _edge_sharpe_annual(net, bench, self.rf, self.dpy)

        draw = permuter(self.research_bars)
        return stat(self.research_bars), np.array([stat(draw(rng)) for _ in range(n)])

    def sealed_returns(self, cfg: dict[str, float]) -> dict[str, Any]:
        """Daily results on the sealed lockbox period.

        Args:
            cfg: Parameters.

        Returns:
            ``net``, ``bench``, ``rf`` (daily arrays), ``start``, ``end`` and ``data_hash``.
        """
        res = run(self.bars, self.strat.positions(self.bars, cfg), self.model, self.rf_bars, with_trades=False)
        lock = ~self.mask
        ts_l = self.bars["ts"][lock]
        rf = np.zeros(int(lock.sum())) if self.rf_bars is None else self.rf_bars.to_numpy()[lock]
        return {
            "net": to_daily(res.returns.to_numpy()[lock], ts_l),
            "bench": to_daily(res.benchmark.to_numpy()[lock], ts_l).to_numpy(),
            "rf": to_daily(rf, ts_l).to_numpy(),
            "start": str(ts_l.iloc[0].date()),
            "end": str(ts_l.iloc[-1].date()),
            "data_hash": store.data_hash(self.bars[lock]),
        }

    def full_daily(self, cfg: dict[str, float]) -> pd.Series:
        """Daily net returns over the whole history, for diversification checks.

        Args:
            cfg: Parameters.

        Returns:
            Returns indexed by date.
        """
        res = run(self.bars, self.strat.positions(self.bars, cfg), self.model, self.rf_bars, with_trades=False)
        return to_daily(res.returns.to_numpy(), self.bars["ts"])


class PanelSubject:
    """A portfolio strategy on a multi-asset panel.

    Attributes match ``BarSubject``; ``scope`` is the universe name.
    """

    kind = "portfolio"

    def __init__(self, panel: Panel, strategy: str, sealed_from: pd.Timestamp, universe: str | None = None) -> None:
        """Prepare a subject from a loaded panel.

        Args:
            panel: Full-history panel.
            strategy: Key of the portfolio strategy registry.
            sealed_from: First lockbox date.
            universe: Universe name; defaults to the panel's.
        """
        self.strat = PORTFOLIOS[strategy]
        self.params: dict[str, Param] = self.strat.params
        self.panel, self.sealed, self.dpy = panel, sealed_from, panel.dpy
        name = universe or panel.name
        self.family, self.strategy, self.scope, self.interval = self.strat.family, strategy, name, "1d"
        self.question = question_of(self.family, name)
        self.mask = np.asarray(panel.ts < sealed_from)
        self.research_panel = panel.slice(self.mask)
        self.days = self.research_panel.ts
        self.rf = self.research_panel.rf
        self.research = (str(self.days[0].date()), str(self.days[-1].date())) if len(self.days) else ("", "")
        self.data_hash = panel_hash(self.research_panel)
        self.cost_bps = float(panel.costs.mean() * 1e4)
        self.meta: dict[str, Any] = {"kind": "portfolio", "source": "panel", "symbol": name, "universe": name,
                                     "interval": "1d", "symbols": panel.symbols, "costs": cost_model_of(panel)}

    @classmethod
    def load(cls, universe: str, strategy: str, sealed_from: pd.Timestamp) -> PanelSubject:
        """Load a named universe and build the subject.

        Args:
            universe: Universe name.
            strategy: Portfolio strategy key.
            sealed_from: First lockbox date.

        Returns:
            The subject.

        Raises:
            KeyError: Unknown universe or strategy.
            FileNotFoundError: An instrument isn't ingested.
        """
        return cls(load_panel(universe), strategy, sealed_from, universe)

    def simulate(self, cfg: dict[str, float]) -> Sim:
        """Daily results of one setting on the research period, weights computed on the full history.

        Args:
            cfg: Parameters.

        Returns:
            A ``Sim``.
        """
        res = run_portfolio(self.panel, self.strat.weights(self.panel, cfg))
        m = self.mask
        return Sim(net=res.net[m], gross=(res.gross + res.cash)[m], bench=res.benchmark[m],
                   turnover=float(res.turnover[m].sum()) / (int(m.sum()) / self.dpy))

    def leak_check(self, cfg: dict[str, float], seed: int) -> dict[str, Any]:
        """Future-shift check on the research panel.

        Args:
            cfg: Parameters.
            seed: Random seed.

        Returns:
            ``passed``, ``cuts`` and ``first_violation``.
        """
        return panel_leakage.future_shift_check(self.strat, self.research_panel, cfg, seed=seed)

    def permutation(self, cfg: dict[str, float], n: int, rng: np.random.Generator) -> tuple[float, np.ndarray]:
        """Edge Sharpe on the real research panel and on ``n`` shuffles of whole days.

        Days are shuffled together across instruments, which keeps their correlation on each day, and each
        instrument's moves are rescaled to its own volatility path (see ``nulls``).

        Args:
            cfg: Parameters.
            n: Number of shuffles.
            rng: Random generator.

        Returns:
            ``(real, null)`` annual edge Sharpes.
        """
        p = self.research_panel

        def stat(panel: Panel) -> float:
            res = run_portfolio(panel, self.strat.weights(panel, cfg))
            return _edge_sharpe_annual(res.net, res.benchmark, panel.rf, self.dpy)

        draw = panel_permuter(p)
        return stat(p), np.array([stat(draw(rng)) for _ in range(n)])

    def sealed_returns(self, cfg: dict[str, float]) -> dict[str, Any]:
        """Daily results on the sealed lockbox period.

        Args:
            cfg: Parameters.

        Returns:
            ``net`` (Series), ``bench``, ``rf``, ``start``, ``end`` and ``data_hash``.
        """
        res = run_portfolio(self.panel, self.strat.weights(self.panel, cfg))
        lock = ~self.mask
        ts_l = self.panel.ts[lock]
        return {"net": pd.Series(res.net[lock], index=ts_l), "bench": res.benchmark[lock], "rf": self.panel.rf[lock],
                "start": str(ts_l[0].date()), "end": str(ts_l[-1].date()),
                "data_hash": panel_hash(self.panel.slice(lock))}

    def full_daily(self, cfg: dict[str, float]) -> pd.Series:
        """Daily net returns over the whole history, for diversification checks.

        Args:
            cfg: Parameters.

        Returns:
            Returns indexed by date.
        """
        res = run_portfolio(self.panel, self.strat.weights(self.panel, cfg))
        return pd.Series(res.net, index=self.panel.ts)


def panel_permuter(panel: Panel):
    """Prepare a panel for shuffling whole days; returns ``draw(rng) -> Panel``.

    Each instrument's overnight gap and open-to-close move are divided by its own local volatility; days are
    shuffled with one permutation for every instrument (keeping same-day co-movement), rescaled to each
    instrument's volatility where they land, and chained from the first close.

    Args:
        panel: Research panel.

    Returns:
        A function drawing one shuffled panel per call.
    """
    o, c = panel.open, panel.close
    gap = np.log(o[1:] / c[:-1])
    body = np.log(c[1:] / o[1:])
    vol = np.column_stack([local_vol(gap[:, j] + body[:, j]) for j in range(panel.n)])
    ug, ub = gap / vol, body / vol

    def draw(rng: np.random.Generator) -> Panel:
        order = rng.permutation(len(gap))
        g, b = np.exp(ug[order] * vol), np.exp(ub[order] * vol)
        opens = c[0] * np.cumprod(g * np.vstack([np.ones((1, panel.n)), b[:-1]]), axis=0)
        closes = opens * b
        return panel.with_prices(np.vstack([o[:1], opens]), np.vstack([c[:1], closes]))

    return draw


def subject_from_meta(meta: dict[str, Any], sealed_from: pd.Timestamp) -> BarSubject | PanelSubject:
    """Rebuild a subject from a report card's metadata.

    Args:
        meta: Card ``meta``.
        sealed_from: First lockbox date.

    Returns:
        The subject.
    """
    if meta.get("kind") == "portfolio":
        return PanelSubject.load(meta["universe"], meta["strategy"], sealed_from)
    return BarSubject.load(meta["source"], meta["symbol"], meta["interval"], meta["strategy"], sealed_from,
                           meta["costs"])
