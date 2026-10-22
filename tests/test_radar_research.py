"""Tests for the radar's pre-registered rules: publication lags, the de-risk logic, calibration markets, HAR test."""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from qlab.backtest.leakage import future_shift_check
from qlab.backtest.strategies import REGISTRY
from qlab.data.bars import normalize_bars
from qlab.gauntlet import calibration
from qlab.radar import research, store


def daily_bars(start: str, n: int, crypto: bool = False) -> pd.DataFrame:
    """Flat daily bars (business days, or every day for crypto)."""
    ts = pd.date_range(start, periods=n, freq="D" if crypto else "B", tz="UTC")
    close = 100 + np.arange(n) * 0.01
    return normalize_bars(pd.DataFrame({"ts": ts, "open": close, "high": close + 1, "low": close - 1,
                                        "close": close, "volume": 1e6}))


def put_views(article: str, views: np.ndarray, start: str = "2016-01-01") -> pd.DatetimeIndex:
    """Store Wikipedia views stamped as published 24 hours after each day ends (the history convention)."""
    ts = pd.date_range(start, periods=len(views), freq="D", tz="UTC")
    store.upsert("wiki", store.slug(article), pd.DataFrame(
        {"ts": ts, "views": views, "published_at": ts + pd.Timedelta(days=2), "fetched_at": ts[-1]}), {})
    return ts


def test_a_decision_sees_every_day_published_since_the_previous_one_and_nothing_later():
    """Days count from their publication; a decision after a gap takes the extreme of the days it learns at once."""
    ts = pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC")
    df = pd.DataFrame({"ts": ts, "published_at": ts + pd.Timedelta(days=2)})
    z = pd.Series([1.0, 5.0, 2.0, 0.0, -4.0, 0.0], index=ts)
    dec = pd.DatetimeIndex(["2026-01-02 20:00", "2026-01-03 20:00", "2026-01-06 20:00", "2026-01-07 20:00"], tz="UTC")
    up = research.newly_known(df, z, dec, "up")
    assert np.isnan(up[0]) and up[1] == 1.0 and up[2] == 5.0 and up[3] == -4.0
    assert research.newly_known(df, z, dec, "down")[2] == 0.0
    assert list(research.latest_known(df, dec)[1:3]) == [ts[0], ts[3]]


def test_the_de_risk_rule_steps_out_for_hold_decisions_from_a_spike():
    """A spike at decision t keeps the position at zero for t .. t + hold - 1; NaN scores stay invested."""
    z = np.array([np.nan, 0, 3, 0, 0, 0, 0, -3, 0])
    assert list(research.derisk(z, 2.5, 3, "up")) == [1, 1, 0, 0, 0, 1, 1, 1, 1]
    assert list(research.derisk(z, 2.5, 2, "down")) == [1, 1, 1, 1, 1, 1, 1, 0, 0]


def calm_views(n: int) -> np.ndarray:
    """Views with a gentle weekly pattern and no outliers."""
    return 1000.0 * (1 + 0.02 * np.sin(np.arange(n) * 2 * np.pi / 7))


def first_bar_after(bars: pd.DataFrame, when: pd.Timestamp) -> pd.Timestamp:
    """The first bar whose 20:00 UTC decision comes at or after ``when``."""
    days = pd.DatetimeIndex(bars["ts"]).normalize()
    return days[days + research.DECISION >= when][0]


@pytest.mark.parametrize("spike_day", [300, 302])
def test_attention_reaches_the_first_spy_decision_after_its_publication_even_over_a_weekend(spike_day):
    """A spike counts from the first weekday decision after it is published (a day after its day ends)."""
    views = calm_views(400)
    views[spike_day] = 50_000
    ts = None
    for a in research.ATTENTION["attention_derisk"]:
        ts = put_views(a, views)
    bars = daily_bars("2016-06-01", 200)
    pos = REGISTRY["attention_derisk"].positions(bars, {"hold": 1})
    out_days = pd.DatetimeIndex(bars["ts"][pos.to_numpy() == 0]).normalize()
    assert list(out_days) == [first_bar_after(bars, ts[spike_day] + pd.Timedelta(days=2))]


def test_the_rules_never_read_the_future_and_wait_for_their_inputs():
    """The look-ahead check passes on stored and synthetic signals; the gauntlet starts at the declared date."""
    for a in research.ATTENTION["crypto_attention_derisk"]:
        put_views(a, 1000 * np.exp(np.random.default_rng(2).normal(0, 0.3, 500)))
    bars = daily_bars("2016-01-01", 450, crypto=True)
    assert future_shift_check(REGISTRY["crypto_attention_derisk"], bars, seed=3)["passed"]
    syn = research.synthetic_signal_bars(3, 0.5, 4)
    for key in ("attention_derisk", "tone_derisk"):
        assert future_shift_check(dataclasses.replace(REGISTRY[key], starts=""), syn, seed=5)["passed"]
    assert REGISTRY["attention_derisk"].starts == research.WIKI_STARTS
    assert REGISTRY["tone_derisk"].starts == research.TONE_STARTS


def test_tone_uses_only_finished_days_and_drops_mean_out():
    """A gloomy finished day steps the rule out the next bar; a partial day is ignored."""
    ts = pd.date_range("2017-01-01", periods=300, freq="D", tz="UTC")
    tone = -1.0 + np.random.default_rng(6).normal(0, 0.1, 300)
    tone[250] = -6.0  # a Friday: first seen by Monday's decision
    partial = np.zeros(300, dtype=bool)
    partial[-1] = True
    tone[-1] = -20.0
    store.upsert("gdelt", "markets", pd.DataFrame({"ts": ts, "tone": tone, "partial": partial,
                                                   "published_at": ts + pd.Timedelta(hours=25),
                                                   "fetched_at": ts[-1]}), {})
    bars = daily_bars("2017-06-01", 120)
    pos = REGISTRY["tone_derisk"].positions(bars, {"hold": 1})
    out_days = pd.DatetimeIndex(bars["ts"][pos.to_numpy() == 0]).normalize()
    assert list(out_days) == [first_bar_after(bars, ts[250] + pd.Timedelta(hours=25))]


def test_synthetic_signal_markets_plant_bursts_only_when_asked():
    """Noise markets have the same volatility after spikes as elsewhere; planted ones much more."""
    for planted, more in ((0.0, False), (0.5, True)):
        b = research.synthetic_signal_bars(40, planted, 7)
        r = np.log(b["close"]).diff().abs().to_numpy()
        z = research.detect.robust_z(b["signal"].to_numpy(), 90)
        after = np.zeros(len(b), dtype=bool)
        for i in np.flatnonzero(np.nan_to_num(z) >= 2.5):
            after[i + 1: i + 6] = True
        ratio = np.nanmean(r[after]) / np.nanmean(r[~after])
        assert (ratio > 2.0) if more else (0.8 < ratio < 1.25)


def test_calibration_builds_signal_subjects_and_true_edges():
    """The gauntlet's calibration can build the radar's synthetic subjects; noise has no edge, bursts some."""
    subj = calibration.make_subject({"family": "signal/attention_derisk", "years": 3, "trend": 0.0, "seed": 1})
    assert subj.strat.starts == "" and subj.question == "attention_derisk:SYNTH:1d"
    assert calibration.true_edge("signal/attention_derisk", 0.0, years=60) < 0.05
    assert calibration.true_edge("signal/tone_derisk", 0.5, years=60) > 0.1


@pytest.mark.parametrize("planted", [0.0, 0.5])
def test_har_test_finds_a_signal_that_precedes_volatility_and_not_one_that_doesnt(planted):
    """The coefficient is positive and significant only when spikes come before bursts."""
    b = research.synthetic_signal_bars(12, planted, 8)
    z = research.detect.robust_z(b["signal"].to_numpy(), 90)
    out = research.har_test(b, z, "1990-01-01", "2010-01-01", "positive")
    assert out["n"] > 2500 and out["r2_with"] >= out["r2_har"]
    if planted:
        assert out["coef"] > 0 and out["p_one_sided"] < 0.01
    else:
        assert out["p_one_sided"] > 0.01
