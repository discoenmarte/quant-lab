"""Tests for intraday mode (M9): the rules on hourly bars, their selector, and the synthetic hourly markets."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qlab.backtest.leakage import future_shift_check
from qlab.backtest.strategies import REGISTRY
from qlab.data.bars import normalize_bars
from qlab.gauntlet import calibration as gcal
from qlab.intraday import rules
from qlab.intraday.synthetic import profile, synthetic_hourly


def test_momentum_holds_the_last_hour_after_an_up_first_hour_and_is_flat_at_midnight():
    """A rising first hour puts the position on the bar before the last hour; every day ends flat."""
    bars = synthetic_hourly(1.0, seed=1)
    pos = rules.intraday_momentum(bars, window=1, hold=1)
    hour = bars["ts"].dt.hour.to_numpy()
    assert set(pos[hour != 22].unique()) == {0.0}  # only the 22:00 bar's position carries into 23:00-24:00
    first = bars[hour == 0].set_index(bars["ts"][hour == 0].dt.normalize())
    went_up = (first["close"] > first["open"]).to_numpy()
    held = pos[hour == 22].to_numpy() == 1.0
    assert (held == went_up[: len(held)]).all() and 0.4 < held.mean() < 0.6
    assert rules.intraday_momentum(bars, window=20, hold=8).eq(0).all()  # no hours left to hold


def test_breakout_enters_after_the_range_once_a_day_and_the_selector_trades_less():
    """A close above the first hours' high enters until the day's end; the volatility selector removes days."""
    bars = synthetic_hourly(1.0, seed=2)
    pos = rules.range_breakout(bars, window=4)
    hour = bars["ts"].dt.hour.to_numpy()
    assert pos[(hour < 4) | (hour == 23)].eq(0).all()
    day = bars["ts"].dt.normalize()
    entries = (pos.diff().fillna(pos) > 0).groupby(day).sum()
    assert entries.max() <= 1 and entries.mean() > 0.3
    calm = rules.range_breakout(bars, window=4, vol_filter=1)
    assert calm.sum() < pos.sum() and (calm <= pos).all()


def test_the_rules_never_read_the_future():
    """The gauntlet's own check: changing bars after a cut leaves every earlier position unchanged."""
    bars = synthetic_hourly(0.6, seed=3)
    for name, params in (("intraday_momentum", {"window": 3, "hold": 2, "vol_filter": 1}),
                         ("range_breakout", {"window": 6, "vol_filter": 1})):
        assert future_shift_check(REGISTRY[name], bars, params, seed=1)["passed"], name


def test_daily_bars_are_refused():
    """An intraday rule on daily bars is an error, not a strategy that never trades."""
    days = pd.bdate_range("2020-01-01", periods=50, tz="UTC")
    bars = normalize_bars(pd.DataFrame({"ts": days, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                                        "volume": 1.0}))
    with pytest.raises(ValueError, match="hourly"):
        rules.intraday_momentum(bars)


def test_synthetic_hours_follow_the_profile_and_momentum_is_planted():
    """Noise markets are busiest in the US afternoon; planted ones move the last hour with the first."""
    assert profile().mean() == pytest.approx(1.0) and profile().argmax() == 15
    noise = synthetic_hourly(3.0, seed=4)
    r = np.log(noise["close"]).diff()
    by_hour = r.groupby(noise["ts"].dt.hour).std()
    assert by_hour[15] > 1.5 * by_hour[3]
    planted = synthetic_hourly(3.0, momentum=0.5, seed=4)
    rp = np.log(planted["close"]).diff().to_numpy()
    h = planted["ts"].dt.hour.to_numpy()
    first = sum(rp[h == k][1:] for k in range(4))
    for k in (20, 23):
        assert np.corrcoef(np.sign(first), rp[h == k][1:])[0, 1] > 0.2
    assert abs(np.corrcoef(np.sign(first), rp[h == 12][1:])[0, 1]) < 0.1  # the middle of the day carries nothing


def test_calibration_builds_hourly_subjects_with_crypto_costs():
    """A calibration task for an intraday family is an hourly subject, judged 365 days a year."""
    subject = gcal.make_subject({"family": "hourly/range_breakout", "years": 0.5, "trend": 0.0, "seed": 9})
    assert subject.interval == "1h" and subject.dpy == 365 and subject.strategy == "range_breakout"
    sim = subject.simulate(subject.strat.defaults())
    assert len(sim.net) == len(subject.days) and np.isfinite(sim.net).all()
