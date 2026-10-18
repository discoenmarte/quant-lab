"""Tests for M3: Sharpe inference, PBO, the edge measure, the grid, the lockbox, thresholds, the ten-stage
pipeline, calibration and the gauntlet and ledger API."""

import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qlab import ledger, lockbox
from qlab.api.main import create_app
from qlab.backtest import metrics, runner
from qlab.backtest.strategies import REGISTRY
from qlab.data import store
from qlab.data.bars import normalize_bars
from qlab.gauntlet import calibration, pipeline, stats, thresholds
from qlab.gauntlet.nulls import permuted_bars, stationary_bootstrap
from qlab.gauntlet.pbo import pbo

FAST = {"n_perm": 30, "n_boot": 60, "grid_max": 40}


def bars_from(log_returns: np.ndarray, start: str = "2010-01-04") -> pd.DataFrame:
    """Daily bars whose closes follow the given log returns, with opens and ranges around them."""
    rng = np.random.default_rng(0)
    n = len(log_returns)
    close = 100 * np.exp(np.cumsum(log_returns))
    open_ = close * np.exp(rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n))
    ts = pd.bdate_range(start, periods=n, tz="UTC")
    return normalize_bars(pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                                        "volume": 1e6}))


@pytest.fixture
def fast_thresholds():
    """Thresholds that keep a gauntlet run under a second."""
    thresholds.update(FAST, "fast tests")


def test_expected_max_sharpe_matches_the_paper():
    """For N = 1000 standard normal trials the approximation gives about 3.25 (exact expectation 3.24)."""
    assert stats.expected_max_sharpe(1000, 1.0) == pytest.approx(3.25, abs=0.01)
    assert stats.expected_max_sharpe(1, 1.0) == 0.0


def test_sharpe_variance_reduces_to_lo_and_grows_with_autocorrelation():
    """Normal iid returns give Lo's (1 + SR²/2)/n; positive autocorrelation widens it, as for trend rules."""
    assert stats.sr_variance(0.1, 1000) == pytest.approx((1 + 0.1**2 / 2) / 1000)
    assert stats.sr_variance(0.1, 1000, rho=0.2) > stats.sr_variance(0.1, 1000) * 1.4


def test_psr_and_false_discovery_behave():
    """PSR is 0.5 at the null and rises with the Sharpe; the false-discovery rate falls with it."""
    assert stats.psr(0.05, 0.05, 1000) == pytest.approx(0.5)
    assert stats.psr(0.1, 0.0, 2500) > 0.99
    low = stats.false_discovery(0.02, 0.5 / math.sqrt(252), 2520, 0.05)
    high = stats.false_discovery(0.08, 0.5 / math.sqrt(252), 2520, 0.05)
    assert 0 < high < low <= 1


def test_dsr_deflates_the_best_of_many_noise_strategies():
    """The best of 200 noise strategies looks good alone (PSR) but not after deflation (DSR)."""
    rng = np.random.default_rng(3)
    r = rng.normal(0, 0.01, (1260, 200))
    sr = r.mean(0) / r.std(0, ddof=1)
    best = r[:, int(np.argmax(sr))]
    alone = stats.psr(stats.sharpe(best), 0.0, len(best))
    deflated = stats.dsr(best, 200, float(np.var(sr, ddof=1)))["dsr"]
    assert alone > 0.95 and deflated < 0.8


def test_pbo_is_a_coin_flip_for_noise_and_low_for_a_real_edge():
    """Picking the in-sample best among noise is no better than chance; a real winner stays the winner."""
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 0.01, (2000, 30))
    assert 0.3 < pbo(noise, max_splits=500)["pbo"] < 0.7
    edge = noise.copy()
    edge[:, 7] += 0.004
    assert pbo(edge, max_splits=500)["pbo"] < 0.1


def test_edge_removes_static_exposure():
    """Holding 60% of the benchmark has no edge; adding a steady alpha does."""
    rng = np.random.default_rng(2)
    b = rng.normal(0.0004, 0.01, 2000)
    passive, beta = metrics.edge(0.6 * b, b)
    assert beta == pytest.approx(0.6)
    assert np.allclose(passive, 0.0)
    skilled, _ = metrics.edge(0.6 * b + 0.0003 + rng.normal(0, 0.002, 2000), b)
    assert metrics.sharpe(skilled, None, 252) > 1.5


def test_permuted_bars_keep_total_return_and_valid_ranges():
    """Shuffling keeps the start, the end and every bar's shape, and the bars stay valid."""
    bars = bars_from(np.random.default_rng(1).normal(0.0003, 0.01, 500))
    pb = permuted_bars(bars, np.random.default_rng(9))
    assert pb["close"].iloc[-1] == pytest.approx(bars["close"].iloc[-1])
    assert (pb["high"] >= pb[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (pb["low"] <= pb[["open", "close"]].min(axis=1) + 1e-9).all()
    assert not np.allclose(pb["close"], bars["close"])


def test_bootstrap_sizes_down_a_risky_stream():
    """A volatile stream needs less than full size to keep its 95th-percentile drawdown within budget."""
    r = np.random.default_rng(4).normal(0.0005, 0.02, 1500)
    boot = stationary_bootstrap(r, n_paths=200, dd_budget=0.2)
    assert boot["max_dd"]["p5"] < boot["max_dd"]["p95"]
    assert 0 < boot["size_for_budget"] < 1


def test_grid_puts_the_candidate_first_and_respects_the_cap():
    """The grid starts with the candidate, has no duplicates, keeps its neighbours and stops at the cap."""
    strat = REGISTRY["trend_sma"]
    grid = pipeline.build_grid(strat, strat.defaults(), cap=120)
    assert grid[0] == strat.defaults()
    assert len(grid) == 120
    keys = [tuple(sorted(g.items())) for g in grid]
    assert len(keys) == len(set(keys))
    assert {"window": 225, "entry_band": 0.01, "exit_band": 0.03} in grid


def test_judge_uses_thresholds_only():
    """The same measurements pass or fail depending only on the thresholds."""
    _, th = thresholds.current()
    v = {"leak": 1.0, "edge": 0.4, "net_cagr": 0.05, "wf_oos": 0.3, "wf_ratio": 0.6, "plateau": 0.8,
         "perm_p": 0.004, "dsr": 0.97, "pbo": 0.1, "regime_share": 0.75, "div_corr": float("nan"),
         "div_gain": float("nan")}
    verdict = pipeline.judge(v, th)
    assert all(x is not False for x in verdict.values())
    assert pipeline.judge({**v, "perm_p": 0.02}, th)["permutation"] is False
    assert pipeline.judge({**v, "perm_p": 0.02}, {**th, "perm_max_p": 0.05})["permutation"] is True


def test_thresholds_are_versioned_and_need_a_reason():
    """Changes create versions, require a reason and are validated."""
    v1, _ = thresholds.current()
    with pytest.raises(ValueError):
        thresholds.update({"perm_max_p": 0.05}, " ")
    with pytest.raises(ValueError):
        thresholds.update({"nope": 1}, "typo")
    with pytest.raises(ValueError):
        thresholds.update({"lockbox_from": "next year"}, "bad date")
    assert thresholds.update({"perm_max_p": 0.05}, "calibration") == v1 + 1
    assert thresholds.current()[1]["perm_max_p"] == 0.05


def test_studio_runs_stop_before_the_lockbox_unless_asked():
    """A default run ends before the sealed date; including the lockbox spends it for that question only."""
    store.write_series("yahoo", "SPY", "1d", bars_from(np.random.default_rng(1).normal(0.0003, 0.01, 4000)))
    _, summary = runner.backtest("yahoo", "SPY", "1d", "trend_sma")
    assert summary["end"] < str(lockbox.sealed_from().date())
    assert not lockbox.spent("trend_sma:SPY:1d")
    _, peek = runner.backtest("yahoo", "SPY", "1d", "trend_sma", include_lockbox=True)
    assert peek["lockbox"]["included"] and lockbox.spent("trend_sma:SPY:1d")
    assert not lockbox.spent("tsmom:SPY:1d")


def test_gauntlet_end_to_end(fast_thresholds):
    """A run saves a ten-stage card, records its grid as trials, and the next run is deflated against it."""
    store.write_series("yahoo", "SPY", "1d", bars_from(np.random.default_rng(8).normal(0.0003, 0.01, 4000)))
    card = pipeline.run_gauntlet("yahoo", "SPY", "1d", "trend_sma")
    assert [s["key"] for s in card["stages"]] == [k for k, _ in pipeline.STAGES]
    assert card["verdict"] in {"candidate", "rejected"}
    assert card["meta"]["n_trials"] == 1
    assert ledger.count_trials("trend_sma:SPY:1d") == card["meta"]["grid_size"]
    assert pipeline.load_card(card["card_id"])["card_id"] == card["card_id"]
    again = pipeline.run_gauntlet("yahoo", "SPY", "1d", "trend_sma", {"window": 150})
    assert again["meta"]["n_trials"] >= card["meta"]["grid_size"]
    funnel = pipeline.funnel()
    assert funnel[0]["reached"] == 2 and funnel[0]["passed"] == 2


def test_lockbox_opens_once_with_confirmation(fast_thresholds, monkeypatch):
    """Only a candidate can open the lockbox, with the question typed out and a reason, and only once."""
    store.write_series("yahoo", "SPY", "1d", bars_from(np.random.default_rng(8).normal(0.0003, 0.01, 4000)))
    card = pipeline.run_gauntlet("yahoo", "SPY", "1d", "trend_sma")
    card["verdict"], card["failed_stage"] = "candidate", None  # pretend it passed stages 1-9
    pipeline._save_card(card)
    with pytest.raises(ValueError, match="type the research question"):
        pipeline.open_lockbox(card["card_id"], "trend_sma", "looks good")
    with pytest.raises(ValueError, match="reason"):
        pipeline.open_lockbox(card["card_id"], "trend_sma:SPY:1d", "")
    opened = pipeline.open_lockbox(card["card_id"], "trend_sma:SPY:1d", "passed stages 1-9")
    assert opened["verdict"] in {"validated", "rejected"}
    assert opened["stages"][-1]["passed"] is not None
    assert lockbox.spent("trend_sma:SPY:1d")
    assert ledger.list_trials(source="lockbox")
    with pytest.raises(ValueError, match="only cards that passed|already spent"):
        pipeline.open_lockbox(card["card_id"], "trend_sma:SPY:1d", "again")


def test_synthetic_markets_have_fat_tails_and_no_edge_without_a_trend():
    """Noise markets look like markets (fat tails, volatility clusters) and give trend rules no edge."""
    bars = calibration.synthetic_bars(20, 0.0, seed=1)
    r = np.log(bars["close"]).diff().dropna()
    assert len(bars) == 20 * 252 and r.kurt() > 1
    assert abs(calibration.true_edge("trend_sma", 0.0, years=200)) < 0.15
    assert calibration.true_edge("trend_sma", 0.4, years=200) > 0.6


def test_calibration_run_and_summary(fast_thresholds):
    """One calibration run produces stage values that the summary re-judges; Wilson intervals are sane."""
    _, th = thresholds.current()
    out = calibration._one({"scenario": "noise_tuned", "family": "sma_cross", "years": 6, "trend": 0.0, "seed": 1,
                            "th": th})
    assert set(out["passed"]) == {k for k, _ in pipeline.STAGES}
    assert len(out["records"]) > 1
    summary = calibration.summarise([out], th)
    assert summary["noise"][0]["n"] == 1
    lo, hi = calibration.wilson(5, 100)
    assert lo < 0.05 < hi


def test_gauntlet_and_ledger_api(fast_thresholds):
    """The API runs the gauntlet, lists cards and trials, and versions thresholds."""
    store.write_series("binance", "BTCUSDT", "1d", bars_from(np.random.default_rng(6).normal(0.001, 0.03, 3000),
                                                             "2015-01-05"))
    client = TestClient(create_app())
    assert len(client.get("/api/gauntlet/stages").json()) == 10
    card = client.post("/api/gauntlet/run", json={"source": "binance", "symbol": "BTCUSDT", "strategy": "tsmom"})
    assert card.status_code == 200, card.text
    card_id = card.json()["card_id"]
    assert client.get(f"/api/gauntlet/cards/{card_id}").json()["meta"]["costs"]["commission_bps"] == 10.0
    assert client.get("/api/gauntlet/cards").json()[0]["card_id"] == card_id
    assert client.get("/api/ledger/counts").json()["by_source"]["gauntlet"] > 1
    assert client.get("/api/ledger/questions").json()[0]["question"] == "tsmom:BTCUSDT:1d"
    bad = client.post("/api/gauntlet/thresholds", json={"changes": {"dsr_min": 0.9}, "reason": ""})
    assert bad.status_code == 400
    ok = client.post("/api/gauntlet/thresholds", json={"changes": {"dsr_min": 0.9}, "reason": "test"}).json()
    assert ok["config"]["dsr_min"] == 0.9
    assert client.get("/api/gauntlet/lockbox").json()["sealed_from"] == lockbox.DEFAULT_FROM
    refused = client.post(f"/api/gauntlet/cards/{card_id}/lockbox", json={"confirm": "x", "reason": "y"})
    assert refused.status_code == 400
