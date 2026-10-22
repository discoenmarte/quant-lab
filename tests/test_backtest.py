"""Tests for M2: engine timing and costs, the strategy contract, look-ahead detection, the runner and the API."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qlab import ledger
from qlab.api.main import create_app
from qlab.backtest import runner
from qlab.backtest.engine import CostModel, run
from qlab.backtest.leakage import future_shift_check
from qlab.backtest.strategies import REGISTRY, Strategy
from qlab.data import store
from qlab.data.bars import normalize_bars

ZERO = CostModel(0.0, 0.0, 0.0)


def random_walk(n: int = 600, seed: int = 1) -> pd.DataFrame:
    """Daily bars from a random walk with realistic open/high/low around the close."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    open_ = close * np.exp(rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.005, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.005, n))
    ts = pd.bdate_range("2015-01-01", periods=n, tz="UTC")
    return normalize_bars(pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                                        "volume": 1e6}))


def test_position_is_filled_at_the_next_open():
    """A decision at bar 5's close is held intraday on bar 6 and overnight into bar 7, never earlier."""
    bars = random_walk(12)
    target = pd.Series(0.0, index=bars.index)
    target.iloc[5] = 1.0
    res = run(bars, target, ZERO)
    assert res.position.iloc[6] == 1.0 and res.position.iloc[:6].eq(0).all()
    intraday_6 = bars["close"].iloc[6] / bars["open"].iloc[6] - 1
    assert res.returns.iloc[6] == pytest.approx(intraday_6)


def test_always_long_matches_the_price_path_from_the_first_fill():
    """Fully invested with no costs, equity grows exactly like close[last] / open[1]."""
    bars = random_walk(300)
    res = run(bars, pd.Series(1.0, index=bars.index), ZERO)
    growth = float(np.prod(1 + res.returns))
    assert growth == pytest.approx(bars["close"].iloc[-1] / bars["open"].iloc[1])


def test_costs_are_charged_on_every_position_change():
    """Flipping between 0 and 1 every bar pays the per-unit cost once per change."""
    bars = random_walk(50)
    target = pd.Series(np.arange(50) % 2, index=bars.index, dtype=float)
    model = CostModel(commission_bps=5, spread_bps=4, slippage_bps=1)
    res = run(bars, target, model)
    changes = np.abs(np.diff(res.position.to_numpy(), prepend=0.0)).sum()
    assert res.costs.sum() == pytest.approx(changes * model.per_unit)


def test_idle_cash_earns_the_risk_free_rate():
    """A flat strategy earns exactly the risk-free rate."""
    bars = random_walk(20)
    rf = pd.Series(0.0001, index=bars.index)
    res = run(bars, pd.Series(0.0, index=bars.index), ZERO, rf)
    assert res.returns.to_numpy() == pytest.approx(np.full(20, 0.0001))


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_registered_strategy_is_free_of_look_ahead(name, monkeypatch):
    """Changing the future never changes past positions, for every strategy in the library, on the bars it trades.

    Intraday rules get hourly bars; the insider rule gets a synthetic insider series (its own alignment is tested
    in test_informed); the radar's rules get a synthetic signal column (their publication lags are tested in
    test_radar_research).
    """
    bars = random_walk(800)
    if name in ("intraday_momentum", "range_breakout"):
        from qlab.intraday.synthetic import synthetic_hourly

        bars = synthetic_hourly(0.3, seed=5)
    if name == "insider_timing":
        from qlab.informed import insiders

        days = pd.bdate_range("2015-01-01", "2030-12-31")
        rng = np.random.default_rng(2)
        series = pd.DataFrame({"buy_filings": rng.poisson(20, len(days)).astype(float),
                               "sell_filings": rng.poisson(60, len(days)).astype(float)}, index=days)
        monkeypatch.setattr(insiders, "series", lambda: series)
    if name in ("attention_derisk", "crypto_attention_derisk", "tone_derisk"):
        bars = bars.assign(signal=np.random.default_rng(3).standard_t(4, len(bars)))
    assert future_shift_check(REGISTRY[name], bars)["passed"]


def test_look_ahead_detector_catches_a_cheating_strategy():
    """A rule that peeks at tomorrow's close is flagged."""
    cheat = Strategy("cheat", "cheat", "uses tomorrow", lambda b: (b["close"].shift(-1) > b["close"]).astype(float))
    assert not future_shift_check(cheat, random_walk(400))["passed"]


def test_trades_are_split_into_round_trips():
    """Two separate long stretches become two trades with entry before exit."""
    bars = random_walk(30)
    target = pd.Series(0.0, index=bars.index)
    target.iloc[3:8] = 1.0
    target.iloc[15:20] = 1.0
    trades = run(bars, target, ZERO).trades
    assert len(trades) == 2
    assert (trades["entry_ts"] < trades["exit_ts"]).all()


def test_runner_records_a_trial_and_builds_a_report():
    """A Studio run lands in the ledger under its research question and the report has every section."""
    store.write_series("yahoo", "SPY", "1d", random_walk(900))
    ff = pd.DataFrame({"ts": pd.bdate_range("2015-01-01", periods=900, tz="UTC"), "mkt_rf": 0.0, "smb": 0.0,
                       "hml": 0.0, "rf": 0.0001})
    store.write_series("french", "FF3", "1d", ff, kind="factors")
    result, summary = runner.backtest("yahoo", "SPY", "1d", "trend_sma", {"window": 100})
    rep = runner.report(result, summary)
    assert summary["question"] == "trend_sma:SPY:1d"
    assert ledger.count_trials("trend_sma:SPY:1d") == 1
    assert set(rep) == {"meta", "stats", "costs", "series", "monthly", "trades"}
    assert rep["stats"]["strategy"]["n_obs"] == 900
    assert runner.load_report(summary["trial_id"])["meta"]["trial_id"] == summary["trial_id"]


def test_backtest_api_lists_strategies_and_runs():
    """The API exposes the registry and runs a backtest that increments N."""
    store.write_series("binance", "BTCUSDT", "1d", random_walk(500))
    client = TestClient(create_app())
    names = {s["name"] for s in client.get("/api/backtest/strategies").json()}
    assert {"buy_and_hold", "trend_sma", "tsmom"} <= names
    body = {"source": "binance", "symbol": "BTCUSDT", "strategy": "sma_cross", "params": {"fast": 20}}
    first = client.post("/api/backtest/run", json=body).json()
    second = client.post("/api/backtest/run", json=body).json()
    assert second["meta"]["n_trials"] == first["meta"]["n_trials"] + 1
    assert first["meta"]["costs"]["commission_bps"] == 10.0  # crypto preset chosen automatically
    assert client.post("/api/backtest/run", json={**body, "strategy": "nope"}).status_code == 404
