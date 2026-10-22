"""Tests for the radar's detectors and their calibration to a false-alarm budget."""

from __future__ import annotations

import numpy as np
import pytest

from qlab.radar import detect


def noise(n: int = 2000, seed: int = 0) -> np.ndarray:
    """Fat-tailed noise with nothing to detect."""
    return np.random.default_rng(seed).standard_t(5, n)


def test_robust_z_uses_only_the_past_and_ignores_outliers_in_its_baseline():
    """Changing later values never changes earlier scores; one huge outlier doesn't hide the next spike."""
    x = noise(500)
    z = detect.robust_z(x, 60)
    y = x.copy()
    y[300:] *= 50
    assert np.allclose(z[:300], detect.robust_z(y, 60)[:300], equal_nan=True)
    x[200], x[230] = 100.0, 8.0
    z = detect.robust_z(x, 60)
    assert z[200] > 50 and z[230] > 5 and np.isnan(z[:30]).all()


def test_robust_z_falls_back_to_the_deviation_when_the_median_spread_is_zero():
    """A mostly-zero count series still scores a burst."""
    x = np.zeros(200)
    x[::10] = 1.0
    x[150] = 30.0
    assert detect.robust_z(x, 60)[150] > 5


def test_cusum_catches_a_small_persistent_shift_and_restarts_after_an_alarm():
    """A one-sigma shift adds up; with a reset level the statistic restarts from zero after each alarm."""
    x = noise(1200, 1)
    x[800:] += 1.0
    z = detect.robust_z(x, 250)
    s = detect.cusum(z, direction="up")
    assert np.nanmax(s[800:830]) > np.nanmax(s[300:790]) and np.nanmax(s[800:830]) > 8
    reset = detect.cusum(z, h=5.0, direction="up")
    assert np.nanmax(reset) < 5.0 + detect.Z_CLIP
    first = int(np.flatnonzero(reset >= 5.0)[0])
    assert reset[first + 1] < reset[first]
    assert np.nanmax(detect.cusum(z, direction="down")[800:830]) < 5  # watching falls ignores the rise


def test_bocpd_sees_a_regime_change_and_stays_low_on_noise():
    """The probability of a recent change jumps after a level shift; a watched direction filters the other."""
    x = noise(1500, 2)
    x[1000:] += 4.0
    z = detect.robust_z(x, 90)
    p = detect.bocpd(z)
    assert np.nanmax(p[1000:1010]) > 0.3
    assert np.nanmedian(p[200:990]) < 0.05
    assert np.nanmax(detect.bocpd(z, direction="down")[1000:1010]) == 0.0


def test_episodes_need_a_quiet_spell_between_them():
    """Values above the threshold separated by fewer than the cooldown are one episode."""
    stat = np.array([0, 5, 0, 5, 0, 0, 0, 0, 0, 0, 5, np.nan, 0], dtype=float)
    assert list(detect.episodes(stat, 4.0, cooldown=5)) == [1, 10]


@pytest.mark.parametrize("detector", detect.DETECTORS)
def test_calibration_meets_the_budget_and_measures_false_alarms_and_delay(detector):
    """History holds about one notable episode a month; shuffled noise alarms no more often; big steps are caught."""
    x = noise(2520, 3)  # ten years of trading days
    months = 2520 / 21
    c = detect.calibrate(x, detector, 90, "up", months, null_reps=4, plant_reps=6)
    assert c["severe"] > c["notable"]
    assert 0.6 <= c["history_rate"] <= 1.3
    assert c["null_rate"] <= 1.5 and c["null_rate_severe"] <= 0.25
    assert c["detect_rate"] >= 0.8 and c["delay"] is not None and c["delay"] <= 5
    assert c["detect_rate_small"] is not None


def test_threshold_search_survives_the_merge_at_low_levels():
    """A threshold low enough to merge everything into one episode is never chosen."""
    x = noise(1000, 4)
    stat = detect.oriented(detect.robust_z(x, 60), "up")
    h = detect.threshold_for(stat, 20)
    assert h > 1.0 and len(detect.episodes(stat, h)) <= 20
    assert detect.threshold_for(np.full(10, np.nan), 3) == float("inf")


def test_unknown_detector_is_refused():
    """A typo can't silently disable detection."""
    with pytest.raises(ValueError, match="unknown detector"):
        detect.statistic(noise(100), "zscore", 30, "up")
