"""Tests for M3b: panels, the weights engine, portfolio strategies, look-ahead, the runner, the portfolio
gauntlet subject and the portfolio API."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qlab import ledger, lockbox
from qlab.api.main import create_app
from qlab.backtest import metrics
from qlab.data import store
from qlab.data.bars import normalize_bars
from qlab.gauntlet import pipeline, thresholds
from qlab.gauntlet.subjects import PanelSubject
from qlab.portfolio import runner
from qlab.portfolio.engine import run_portfolio
from qlab.portfolio.leakage import future_shift_check
from qlab.portfolio.panel import Panel, load_panel
from qlab.portfolio.strategies import REGISTRY, PortfolioStrategy
from qlab.portfolio.synthetic import synthetic_panel


def write_etf(symbol: str, n: int, seed: int, start: str = "2010-01-04") -> None:
    """Store random-walk daily bars for an ETF symbol."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    open_ = close * np.exp(rng.normal(0, 0.002, n))
    ts = pd.bdate_range(start, periods=n, tz="UTC")
    store.write_series("yahoo", symbol, "1d", normalize_bars(pd.DataFrame({
        "ts": ts, "open": open_, "high": np.maximum(open_, close) * 1.002, "low": np.minimum(open_, close) * 0.998,
        "close": close, "volume": 1e6})))


def tiny_panel(n: int = 60) -> Panel:
    """Two instruments with known prices and zero costs."""
    ts = pd.bdate_range("2020-01-01", periods=n, tz="UTC")
    close = np.column_stack([np.linspace(100, 160, n), np.linspace(50, 40, n)])
    open_ = close * 0.999
    return Panel("tiny", ts, ["A", "B"], ["yahoo", "yahoo"], ["equity", "equity"], open_, close, np.zeros(n),
                 np.zeros(2))


def test_weights_fill_at_the_next_open():
    """A weight decided at date 5's close earns nothing before date 6's open."""
    p = tiny_panel()
    w = np.zeros((60, 2))
    w[5, 0] = 1.0
    res = run_portfolio(p, w)
    assert res.held[6, 0] == 1.0 and not res.held[:6].any()
    assert res.net[6] == pytest.approx(p.close[6, 0] / p.open[6, 0] - 1.0)


def test_equal_weight_matches_its_benchmark_exactly():
    """Equal weight through the engine is the benchmark, so its edge is exactly zero."""
    p = synthetic_panel(5, 4, 0.0, seed=2)
    res = run_portfolio(p, REGISTRY["equal_weight"].weights(p))
    assert np.allclose(res.net, res.benchmark)


def test_costs_and_leverage_financing():
    """Costs are charged per unit of weight change; borrowed cash pays the rate plus the spread."""
    p = tiny_panel()
    p = Panel(p.name, p.ts, p.symbols, p.sources, p.asset_class, p.open, p.close, np.full(60, 0.0001),
              np.array([0.001, 0.002]))
    w = np.zeros((60, 2))
    w[10:, 0] = 1.5
    res = run_portfolio(p, w, financing_spread=0.0252)
    assert res.costs[11] == pytest.approx(1.5 * 0.001)
    assert res.cash[20] == pytest.approx(-0.5 * (0.0001 + 0.0252 / 252))


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_portfolio_strategy_is_free_of_look_ahead(name):
    """Changing future prices never changes past weights."""
    assert future_shift_check(REGISTRY[name], synthetic_panel(6, 5, 0.0, seed=4))["passed"]


def test_look_ahead_detector_catches_a_cheating_portfolio():
    """A rule that buys tomorrow's winners is flagged."""
    def peek(panel: Panel) -> np.ndarray:
        up = np.vstack([panel.close[1:] > panel.close[:-1], np.zeros((1, panel.n), dtype=bool)])
        return up / panel.n

    cheat = PortfolioStrategy("cheat", "cheat", "peeks", peek)
    assert not future_shift_check(cheat, synthetic_panel(6, 5, 0.0, seed=4))["passed"]


def test_volatility_targeting_hits_its_target_and_respects_the_cap():
    """A volatility-targeted book lands near its target and never exceeds 2x gross."""
    p = synthetic_panel(15, 6, 0.0, seed=8)
    w = REGISTRY["risk_parity"].weights(p, {"target_vol": 0.10})
    res = run_portfolio(p, w)
    assert np.abs(w).sum(axis=1).max() <= 2.0 + 1e-9
    assert 0.07 < metrics.summary(pd.Series(res.net[300:]), None, 252)["vol"] < 0.13


def test_panel_starts_when_every_instrument_exists():
    """A universe's panel begins at the latest first date and keeps only shared dates."""
    write_etf("SPY", 800, 1, "2010-01-04")
    write_etf("TLT", 600, 2, "2010-09-01")
    p = load_panel([("yahoo", "SPY"), ("yahoo", "TLT")])
    assert p.ts[0] == pd.Timestamp("2010-09-01", tz="UTC")
    assert p.close.shape == (len(p.ts), 2) and np.isfinite(p.close).all()


def test_runner_stops_before_the_lockbox_and_records_trials(monkeypatch):
    """A Portfolio Lab run ends before the sealed date, is a ledger trial, and its report has every section."""
    for i, s in enumerate(["SPY", "EFA", "TLT"]):
        write_etf(s, 4000, i + 10, "2010-01-04")
    monkeypatch.setitem(runner.UNIVERSES, "tiny", {"members": [("yahoo", s) for s in ["SPY", "EFA", "TLT"]],
                                                   "description": "test"})
    panel, res, summary = runner.backtest("tiny", "trend_filter")
    assert summary["end"] < str(lockbox.sealed_from().date())
    assert ledger.count_trials("trend_filter:tiny:1d") == 1
    rep = runner.report(panel, res, summary)
    assert {"meta", "stats", "series", "weights", "latest", "contribution", "monthly", "costs"} <= set(rep)
    assert runner.load_report(summary["trial_id"])["meta"]["trial_id"] == summary["trial_id"]


def test_portfolio_gauntlet_on_a_synthetic_panel():
    """The gauntlet judges a portfolio: noise fails, a strong planted trend passes."""
    thresholds.update({"n_perm": 60, "n_boot": 60, "grid_max": 40}, "fast tests")
    _, th = thresholds.current()
    for trend, admitted in ((0.0, False), (0.35, True)):
        p = synthetic_panel(16, 8, trend, seed=21)
        sub = PanelSubject(p, "trend_filter", p.ts[-1] + pd.Timedelta(days=1), "synthetic")
        out = pipeline.evaluate(sub, sub.strat.defaults(), th=th)
        assert (out["failed_stage"] is None) == admitted
        assert len(out["records"]) == out["grid_size"]


def test_portfolio_api_and_gauntlet_endpoint(monkeypatch):
    """The API lists universes and strategies, runs a portfolio, and judges one."""
    thresholds.update({"n_perm": 60, "n_boot": 60, "grid_max": 30}, "fast tests")
    for i, s in enumerate(["SPY", "EFA", "TLT", "GLD"]):
        write_etf(s, 4200, i + 30, "2008-01-02")
    monkeypatch.setitem(runner.UNIVERSES, "tiny", {"members": [("yahoo", s) for s in ["SPY", "EFA", "TLT", "GLD"]],
                                                   "description": "test"})
    client = TestClient(create_app())
    assert any(u["name"] == "tiny" and u["first"] for u in client.get("/api/portfolio/universes").json())
    assert {"tsmom", "trend_filter", "xs_momentum"} <= {s["name"] for s in
                                                        client.get("/api/portfolio/strategies").json()}
    rep = client.post("/api/portfolio/run", json={"universe": "tiny", "strategy": "xs_momentum", "params": {"top": 2}})
    assert rep.status_code == 200, rep.text
    assert rep.json()["meta"]["question"] == "xs_momentum:tiny:1d"
    card = client.post("/api/gauntlet/run", json={"kind": "portfolio", "universe": "tiny", "strategy": "trend_filter"})
    assert card.status_code == 200, card.text
    assert card.json()["meta"]["kind"] == "portfolio" and len(card.json()["stages"]) == 10
    assert client.post("/api/portfolio/run", json={"universe": "nope"}).status_code == 404


def test_a_preregistered_batch_is_deflated_against_itself_not_by_order(monkeypatch):
    """Every candidate in a batch counts the whole batch as its trials, whatever order they run in."""
    from qlab import audit

    thresholds.update({"n_perm": 60, "n_boot": 60, "grid_max": 30}, "fast tests")
    for i, s in enumerate(["SPY", "EFA", "TLT", "GLD"]):
        write_etf(s, 4200, i + 50, "2008-01-02")
    monkeypatch.setitem(runner.UNIVERSES, "tiny", {"members": [("yahoo", s) for s in ["SPY", "EFA", "TLT", "GLD"]],
                                                   "description": "test"})
    sealed = lockbox.sealed_from()
    entries = [(PanelSubject.load("tiny", s, sealed), None) for s in ("trend_filter", "xs_momentum", "tsmom")]
    cards = pipeline.judge_batch(entries, "published defaults, chosen before looking")
    assert [c["meta"]["n_trials"] for c in cards] == [3, 3, 3]
    assert len({c["meta"]["batch"] for c in cards}) == 1
    assert any(e["action"] == "gauntlet.preregister" for e in audit.recent(50))
