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
from ..backtest.strategies import REGISTRY, Param, Strategy
from ..data import store
from ..data.ingest import SOURCES
from ..portfolio import leakage as panel_leakage
from ..portfolio.engine import run_portfolio
from ..portfolio.panel import Panel, cost_model_of, load_panel
from ..portfolio.runner import check_fit, panel_hash, question_of
from ..portfolio.strategies import REGISTRY as PORTFOLIOS
from ..portfolio.strategies import PortfolioStrategy
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
        strategy: str | Strategy,
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
            strategy: Registry key, or a strategy object (a mined rule compiled by ``qlab.mining``).
            sealed_from: First lockbox date.
            source: Data source (or ``"synthetic"``).
            symbol: Instrument.
            interval: Bar interval.
            model: Cost model.
            rf: Risk-free return per bar, aligned with ``bars``; None means zero.
            dpy: Days per year.
        """
        self.strat = REGISTRY[strategy] if isinstance(strategy, str) else strategy
        starts = getattr(self.strat, "starts", "")
        if starts:  # inputs that begin later than prices: earlier years aren't evidence either way
            keep = (bars["ts"] >= pd.Timestamp(starts, tz="UTC")).to_numpy()
            bars = bars[keep].reset_index(drop=True)
            rf = None if rf is None else pd.Series(rf.to_numpy()[keep], index=bars.index)
        self.params: dict[str, Param] = self.strat.params
        self.family, self.strategy, self.scope, self.interval = self.strat.family, self.strat.name, symbol, interval
        self.session = getattr(self.strat, "session", "all")  # mined rules may hold only overnight or by day
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
    def load(cls, source: str, symbol: str, interval: str, strategy: str | Strategy, sealed_from: pd.Timestamp,
             costs: str | dict[str, float] | CostModel = "auto") -> BarSubject:
        """Load stored bars and build the subject.

        Args:
            source: Data source.
            symbol: Instrument.
            interval: Bar interval.
            strategy: Registry key or strategy object.
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
        res = run(self.bars, self.strat.positions(self.bars, cfg), self.model, self.rf_bars, with_trades=False,
                  session=self.session)
        m = self.mask
        pos = res.position.to_numpy()[m]
        # A session strategy round-trips every day it holds, which position changes don't show; its costs do.
        traded = (float(res.costs.to_numpy()[m].sum()) / self.model.per_unit if self.session != "all"
                  and self.model.per_unit > 0 else float(np.abs(np.diff(pos, prepend=0.0)).sum()))
        return Sim(
            net=to_daily(res.returns.to_numpy()[m], self.ts_r).to_numpy(),
            gross=to_daily((res.gross + res.cash).to_numpy()[m], self.ts_r).to_numpy(),
            bench=to_daily(res.benchmark.to_numpy()[m], self.ts_r).to_numpy(),
            turnover=traded / (len(self.days) / self.dpy),
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
            res = run(b, self.strat.positions(b, cfg), self.model, rf_bars, with_trades=False, session=self.session)
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
        res = run(self.bars, self.strat.positions(self.bars, cfg), self.model, self.rf_bars, with_trades=False,
                  session=self.session)
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
        res = run(self.bars, self.strat.positions(self.bars, cfg), self.model, self.rf_bars, with_trades=False,
                  session=self.session)
        return to_daily(res.returns.to_numpy(), self.bars["ts"])


class PanelSubject:
    """A portfolio strategy on a multi-asset panel.

    Attributes match ``BarSubject``; ``scope`` is the universe name.
    """

    kind = "portfolio"

    def __init__(self, panel: Panel, strategy: str | PortfolioStrategy, sealed_from: pd.Timestamp,
                 universe: str | None = None) -> None:
        """Prepare a subject from a loaded panel.

        Args:
            panel: Full-history panel.
            strategy: Key of the portfolio strategy registry, or a strategy object (a mined rule).
            sealed_from: First lockbox date.
            universe: Universe name; defaults to the panel's.

        Raises:
            ValueError: The strategy is built for the other kind of universe (static or dynamic).
        """
        self.strat = PORTFOLIOS[strategy] if isinstance(strategy, str) else strategy
        check_fit(self.strat, panel)
        self.params: dict[str, Param] = self.strat.params
        self.panel, self.sealed, self.dpy = panel, sealed_from, panel.dpy
        name = universe or panel.name
        self.family, self.strategy, self.scope, self.interval = self.strat.family, self.strat.name, name, "1d"
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
        # How the permutation stage words its null (see ``permutation``).
        self.null_note = "null worlds deal each day's moves out again across coins" if panel.dynamic else ""

    @classmethod
    def load(cls, universe: str, strategy: str | PortfolioStrategy, sealed_from: pd.Timestamp) -> PanelSubject:
        """Load a named universe and build the subject.

        Args:
            universe: Universe name.
            strategy: Portfolio strategy key or strategy object.
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
        """Edge Sharpe on the real research panel and on ``n`` null worlds.

        Static panels: whole days are shuffled together across instruments, which keeps their correlation on
        each day, and each instrument's moves are rescaled to its own volatility path (see ``nulls``). Dynamic
        panels: each day's moves are dealt out again across instruments (``cross_sectional_permuter``), which
        tests what a cross-sectional rule claims: that a coin's own characteristics predict its next move
        relative to the others (decision 0006).

        Args:
            cfg: Parameters.
            n: Number of null worlds.
            rng: Random generator.

        Returns:
            ``(real, null)`` annual edge Sharpes.
        """
        p = self.research_panel

        def stat(panel: Panel) -> float:
            res = run_portfolio(panel, self.strat.weights(panel, cfg))
            return _edge_sharpe_annual(res.net, res.benchmark, panel.rf, self.dpy)

        draw = cross_sectional_permuter(p) if p.dynamic else panel_permuter(p)
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
    instrument's volatility where they land, and chained from the first close. Dynamic panels go through
    ``masked_permuter``, which keeps the availability mask, dollar volume and membership in place.

    Args:
        panel: Research panel.

    Returns:
        A function drawing one shuffled panel per call.
    """
    if panel.dynamic or not panel.avail.all():
        return masked_permuter(panel)
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


def masked_permuter(panel: Panel):
    """Shuffle whole days of a panel whose instruments enter and leave, keeping who trades when in place.

    The null world keeps the calendar of listings, delistings and halts, every instrument's volatility path,
    its dollar volume and the universe's membership; only the order of the moves is random. A day's move of
    instrument j (a simple return, see ``_unit_moves``), in units of j's local volatility, is split into the
    day's cross-sectional mean (the market)
    and j's residual. All instruments take the market part of the same source day, which keeps same-day
    co-movement. Each takes its own residual from that day when it traded then, and otherwise from a random
    day of its own history (an instrument shuffled onto a date before its listing has no move of its own to
    use). Prices are rebuilt within each run of consecutive bars, starting from the run's real first bar.

    Args:
        panel: Research panel with an availability mask.

    Returns:
        ``draw(rng) -> Panel``.
    """
    valid, vol, ug, ub, run_start, log_o, log_c = _unit_moves(panel)
    t_len, n = valid.shape
    days = np.flatnonzero(valid.any(axis=1))
    with np.errstate(invalid="ignore"):
        mg, mb = np.nanmean(ug[days], axis=1), np.nanmean(ub[days], axis=1)
    eg, eb = ug[days] - mg[:, None], ub[days] - mb[:, None]  # (D, N), NaN where j didn't trade on that day
    own = [np.flatnonzero(np.isfinite(eg[:, j])) for j in range(n)]
    counts = np.array([len(x) for x in own])
    offsets = np.concatenate([[0], np.cumsum(counts)[:-1]])
    pool = np.concatenate(own) if counts.sum() else np.zeros(0, dtype=int)

    def draw(rng: np.random.Generator) -> Panel:
        src = rng.permutation(len(days))  # target day days[k] takes the moves of source day days[src[k]]
        e_g, e_b = eg[src], eb[src]
        missing = ~np.isfinite(e_g) & valid[days]
        if missing.any():
            jj = np.nonzero(missing)[1]
            ok = counts[jj] > 0
            pick = np.zeros(len(jj), dtype=int)
            pick[ok] = pool[offsets[jj[ok]] + (rng.random(ok.sum()) * counts[jj[ok]]).astype(int)]
            e_g[missing] = np.where(ok, eg[pick, jj], 0.0)
            e_b[missing] = np.where(ok, eb[pick, jj], 0.0)
        g_new = np.zeros((t_len, n))
        b_new = np.zeros((t_len, n))
        g_new[days] = np.where(valid[days], (mg[src, None] + e_g) * vol[days], 0.0)
        b_new[days] = np.where(valid[days], (mb[src, None] + e_b) * vol[days], 0.0)
        return _rebuild(panel, valid, g_new, b_new, run_start, log_o, log_c)

    return draw


def _unit_moves(panel: Panel) -> tuple[np.ndarray, ...]:
    """Each instrument's daily moves in units of its own local volatility, for the masked shuffles.

    Moves are simple returns (overnight gap and open-to-close body), not log returns. A log move carries its
    instrument's volatility drag (about −σ²/2), so handing one from a volatile coin to a calm one would give the
    calm coin too much drag: in the null worlds calm coins would lose and volatile coins gain on average, and a
    low-volatility rule with no edge would beat its null 40% of the time (decision 0006). Simple returns keep a
    coin with zero expected return at zero wherever its moves land.

    Args:
        panel: Panel with an availability mask.

    Returns:
        ``(valid, vol, ug, ub, run_start, log_o, log_c)``: return days (a bar today and yesterday), local
        volatility, unit gaps and bodies (NaN off return days), the first row of each run of bars for every cell,
        and log prices (zero where there is no bar).
    """
    a, o, c = panel.avail, panel.open, panel.close
    t_len, n = c.shape
    valid = np.zeros((t_len, n), dtype=bool)
    valid[1:] = a[1:] & a[:-1]
    gap = np.zeros((t_len, n))
    with np.errstate(invalid="ignore", divide="ignore"):
        gap[1:] = np.where(valid[1:], o[1:] / c[:-1] - 1.0, 0.0)
        body = np.where(valid, c / o - 1.0, 0.0)
    vol = np.ones((t_len, n))
    for j in range(n):
        rows = np.flatnonzero(valid[:, j])
        if len(rows):
            vol[rows, j] = local_vol(np.log1p(gap[rows, j]) + np.log1p(body[rows, j]))
    ug, ub = np.where(valid, gap / vol, np.nan), np.where(valid, body / vol, np.nan)
    run_start = np.maximum.accumulate(np.where(a & ~valid, np.arange(t_len)[:, None], 0), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        log_o, log_c = np.log(np.where(a, o, 1.0)), np.log(np.where(a, c, 1.0))
    return valid, vol, ug, ub, run_start, log_o, log_c


def _rebuild(panel: Panel, valid: np.ndarray, g_new: np.ndarray, b_new: np.ndarray, run_start: np.ndarray,
             log_o: np.ndarray, log_c: np.ndarray) -> Panel:
    """Chain new gaps and bodies (simple returns) into prices, run by run, from each run's real first bar.

    Args:
        panel: The original panel (its mask and first bars are kept).
        valid: Return days.
        g_new: New simple gaps, ``(T, N)``; floored at −95%.
        b_new: New simple bodies, ``(T, N)``; floored at −95%.
        run_start: First row of each cell's run.
        log_o: Real log opens.
        log_c: Real log closes.

    Returns:
        The panel with the new prices.
    """
    n = panel.n
    cols = np.arange(n)
    lg = np.log1p(np.maximum(g_new, -0.95))
    lb = np.log1p(np.maximum(b_new, -0.95))
    step = np.where(valid, lg + lb, 0.0)
    cum = np.cumsum(step, axis=0)
    lc = log_c[run_start, cols] + cum - cum[run_start, cols]
    lo = np.where(valid, np.vstack([np.zeros((1, n)), lc[:-1]]) + lg, log_o)
    a = panel.avail
    return panel.with_prices(np.where(a, np.exp(lo), np.nan), np.where(a, np.exp(lc), np.nan))


def cross_sectional_permuter(panel: Panel):
    """Deal each day's moves out again across instruments, keeping the calendar, volatility paths and volume.

    The null hypothesis of a cross-sectional rule is that no coin's own characteristics (its past returns, its
    volatility, its size) say anything about its next move relative to the others. So on each day, the moves of
    the instruments that traded, in units of each one's local volatility, are handed out again at random and
    rescaled to the volatility of the instrument that receives them. Members of the universe (as of the previous
    close, the universe the day's positions were chosen from) trade moves among themselves, and the other pairs
    among themselves, so the opportunity set keeps its own spread of moves. The day's market move, the order of
    days, every volatility path, dollar volume, membership and the mask stay; only which coin got which move is
    random. Unlike a shuffle of days, this also removes persistent differences between coins (small coins
    earning more for years), which a day shuffle keeps in its null worlds, so it can find a factor premium and
    not only timing (decision 0006).

    Args:
        panel: Research panel with an availability mask.

    Returns:
        ``draw(rng) -> Panel``.
    """
    valid, vol, ug, ub, run_start, log_o, log_c = _unit_moves(panel)
    group = np.zeros(valid.shape, dtype=bool)
    group[1:] = panel.member[:-1]
    code = np.where(valid, np.where(group, 0, 1), 2)  # members first, then the other pairs, then no move
    order_valid = np.argsort(code, axis=1, kind="stable")  # each row's cells by group, in column order
    ugz, ubz = np.nan_to_num(ug), np.nan_to_num(ub)

    def draw(rng: np.random.Generator) -> Panel:
        donor = np.argsort(code + rng.random(valid.shape), axis=1)  # same groups, random order inside each
        g_unit, b_unit = np.empty_like(ugz), np.empty_like(ubz)
        np.put_along_axis(g_unit, order_valid, np.take_along_axis(ugz, donor, axis=1), axis=1)
        np.put_along_axis(b_unit, order_valid, np.take_along_axis(ubz, donor, axis=1), axis=1)
        return _rebuild(panel, valid, np.where(valid, g_unit * vol, 0.0), np.where(valid, b_unit * vol, 0.0),
                        run_start, log_o, log_c)

    return draw


def subject_from_meta(meta: dict[str, Any], sealed_from: pd.Timestamp) -> BarSubject | PanelSubject:
    """Rebuild a subject from a report card's metadata.

    Args:
        meta: Card ``meta``.
        sealed_from: First lockbox date.

    Returns:
        The subject. A mined strategy (``mined_...``) is rebuilt from its stored spec.
    """
    strategy: Any = meta["strategy"]
    if str(strategy).startswith("mined_"):
        from ..mining.grammar import load_strategy  # mining builds on the gauntlet: import here, one-way layers

        strategy = load_strategy(strategy)
    if meta.get("kind") == "portfolio":
        return PanelSubject.load(meta["universe"], strategy, sealed_from)
    return BarSubject.load(meta["source"], meta["symbol"], meta["interval"], strategy, sealed_from, meta["costs"])
