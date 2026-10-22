"""Tests for the radar's scan: signals, anomaly episodes, the alert rule, coincidences and the opt-in pause."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from qlab import alerts
from qlab.broker import controls
from qlab.data import store as lake
from qlab.data.bars import normalize_bars
from qlab.radar import scan, signals, store, watchlist

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def put(source: str, name: str, col: str, values: np.ndarray, end: str = "2026-10-21", label: str = "") -> None:
    """Store a daily radar series ending on ``end``."""
    ts = pd.date_range(end=end, periods=len(values), freq="D", tz="UTC")
    df = pd.DataFrame({"ts": ts, col: values, "published_at": ts + pd.Timedelta(days=1),
                       "fetched_at": ts + pd.Timedelta(days=1)})
    store.upsert(source, name, df, {"label": label or name, "category": "x"})


def calm(n: int, seed: int, level: float = 1000.0) -> np.ndarray:
    """Daily counts with ordinary noise around a level."""
    return level * np.exp(np.random.default_rng(seed).normal(0, 0.1, n))


def spy(n: int = 900, crash_from: int | None = None) -> None:
    """Store SPY daily bars in the lake, with an optional crash at the end."""
    rng = np.random.default_rng(9)
    r = rng.normal(0.0003, 0.01, n)
    if crash_from is not None:
        r[crash_from:] = -0.09
    close = 100 * np.exp(np.cumsum(r))
    ts = pd.bdate_range(end="2026-10-21", periods=n, tz="UTC")
    lake.write_series("yahoo", "SPY", "1d", normalize_bars(pd.DataFrame(
        {"ts": ts, "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6})))


@pytest.fixture
def small_watchlist(isolated_lab):
    """A watchlist with three Wikipedia articles, one HN keyword, SPY prices and no scenarios."""
    watchlist.path().parent.mkdir(parents=True, exist_ok=True)
    watchlist.path().write_text(json.dumps({
        "wiki": [{"article": "Bank_run", "label": "Wikipedia: Bank run", "category": "macro"},
                 {"article": "Recession", "label": "Wikipedia: Recession", "category": "macro"},
                 {"article": "Bitcoin", "label": "Wikipedia: Bitcoin", "category": "crypto"}],
        "hn": [{"keyword": "AI", "label": "HN: AI", "category": "ai"}], "gdelt": [], "scenarios": [],
        "prices": {"yahoo": ["SPY"]}}))
    return isolated_lab


def test_signals_are_built_from_what_is_stored(small_watchlist):
    """Each stored series becomes its signal with the transform and direction of the table."""
    put("wiki", "bank_run", "views", calm(400, 1))
    put("hn", "ai", "stories", calm(400, 2, 50))
    spy()
    sigs = {s.key: s for s in signals.build()}
    assert {"wiki/bank_run", "hn/ai", "prices/SPY:return", "prices/SPY:rv"} <= set(sigs)
    assert sigs["wiki/bank_run"].direction == "up" and sigs["prices/SPY:return"].detectors == ("robust_z",)
    assert np.allclose(sigs["wiki/bank_run"].x, np.log1p(sigs["wiki/bank_run"].level))


def test_a_spike_opens_one_severe_episode_alerts_once_in_plain_words_and_closes(small_watchlist):
    """The newest value far above normal opens a severe anomaly; the next scan doesn't alert again; calm closes it."""
    views = calm(800, 3)
    views[-1] = 60_000
    put("wiki", "bank_run", "views", views, label="Wikipedia: Bank run")
    out = scan.run(NOW, notify=False)
    assert out["calibration"] and out["open"]["severe"] >= 1
    a = scan.anomalies("open")[0]
    assert a["signal"] == "wiki/bank_run" and a["severity"] == "severe" and a["label"] == "Wikipedia: Bank run"
    sent = [r for r in alerts.recent() if r["rule"] == "radar"]
    assert len(sent) == 1
    msg = sent[0]["message"]
    assert "What moved: Wikipedia: Bank run was 60,000" in msg and "How unusual:" in msg and "Since: 2026-10-21" in msg
    assert "Response (committed in advance):" in msg
    scan.run(NOW + timedelta(minutes=15), notify=False)
    assert len([r for r in alerts.recent() if r["rule"] == "radar"]) == 1
    calm_again = np.append(views, 1000.0)
    put("wiki", "bank_run", "views", calm_again, end="2026-10-22")
    scan.run(NOW + timedelta(days=1), notify=False)
    assert not [r for r in scan.anomalies("open") if r["signal"] == "wiki/bank_run" and r["detector"] == "robust_z"]
    assert scan.anomalies("closed")


def test_stale_signals_have_no_current_anomaly(small_watchlist):
    """A spike on a series last updated weeks ago is history, not news."""
    views = calm(800, 4)
    views[-1] = 60_000
    put("wiki", "bank_run", "views", views, end="2026-09-01")
    out = scan.run(NOW, notify=False)
    assert out["open"] == {"severe": 0, "notable": 0}
    assert all(r["stale"] for r in out["readings"])


def test_alerts_are_rate_limited_per_day(small_watchlist, monkeypatch):
    """Beyond the daily limit the rest are summarised in one message."""
    monkeypatch.setattr(scan, "MAX_ALERTS_PER_DAY", 1)
    for i, name in enumerate(("bank_run", "recession")):
        v = calm(800, 10 + i)
        v[-1] = 80_000
        put("wiki", name, "views", v)
    scan.run(NOW, notify=False)
    titles = [r["title"] for r in alerts.recent() if r["rule"] == "radar"]
    assert len(titles) == 2 and any("more severe anomalies" in t for t in titles)


def spikes_everywhere() -> None:
    """Severe anomalies in three signals from three categories, one of them market data."""
    for i, name in enumerate(("bank_run", "bitcoin")):
        v = calm(800, 20 + i)
        v[-1] = 90_000
        put("wiki", name, "views", v)
    spy(crash_from=899)


def test_a_coincidence_alerts_but_pauses_nothing_unless_the_owner_turned_autopause_on(small_watchlist):
    """Without QLAB_RADAR_AUTOPAUSE the trading state is untouched."""
    spikes_everywhere()
    out = scan.run(NOW, notify=False)
    assert out["coincidence"]["active"] and not out["paused"]
    assert controls.state(NOW)["mode"] == "active"
    titles = [r["title"] for r in alerts.recent() if r["rule"] == "radar"]
    assert "Radar: several severe anomalies at once" in titles


def test_autopause_pauses_new_orders_on_a_coincidence_and_never_resumes(small_watchlist, monkeypatch):
    """With the setting on, a coincidence that includes market data pauses; nothing in the radar resumes."""
    monkeypatch.setenv("QLAB_RADAR_AUTOPAUSE", "1")
    spikes_everywhere()
    out = scan.run(NOW, notify=False)
    st = controls.state(NOW)
    assert out["paused"] and st["mode"] == "paused" and st["reason"].startswith("radar:")
    controls.resume("all", "owner checked", controls.RESUME_WORD, now=NOW + timedelta(minutes=1))
    later = NOW + timedelta(days=2)
    scan.run(later, notify=False)
    assert controls.state(later)["mode"] == "active"  # the owner's resume stands; the radar never resumes or re-adds


def test_the_report_line_says_quiet_or_names_what_is_open(small_watchlist):
    """Before a scan, quiet, and with anomalies."""
    assert "no scan yet" in scan.line()["text"]
    put("wiki", "bank_run", "views", calm(800, 30))
    scan.run(NOW, notify=False)
    assert "quiet" in scan.line()["text"] or "anomalies open" in scan.line()["text"]
    v = calm(800, 31)
    v[-1] = 90_000
    put("wiki", "recession", "views", v, label="Wikipedia: Recession")
    scan.run(NOW, notify=False)
    assert "Wikipedia: Recession" in scan.line()["text"]
