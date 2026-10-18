"""Tests for M7a: session times, the paper book's decisions (and the refusals that keep them ahead of outcomes),
scoring through the engines, the forward statistics, the decay alarm and the paper API."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qlab import db, ledger, lockbox
from qlab.api.main import create_app
from qlab.backtest.engine import CostModel
from qlab.backtest.engine import run as run_bars
from qlab.data import store
from qlab.data.bars import normalize_bars
from qlab.gauntlet import pipeline
from qlab.paper import book, monitor, score, sessions
from qlab.portfolio import panel as panel_mod
from qlab.portfolio.engine import run_portfolio
from qlab.portfolio.panel import load_panel
from qlab.portfolio.strategies import REGISTRY as PORTFOLIOS

N_DAYS = 1300
START = "2015-01-02"
LOCKBOX = "2018-06-01"
COSTS = {"commission_bps": 0.0, "spread_bps": 2.0, "slippage_bps": 1.0}


def prices(seed: int, n: int = N_DAYS + 30) -> pd.DataFrame:
    """Random-walk daily bars on business days, more than the tests store at first."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n)))
    open_ = close * np.exp(rng.normal(0, 0.003, n))
    return pd.DataFrame({"ts": pd.bdate_range(START, periods=n, tz="UTC"), "open": open_,
                         "high": np.maximum(open_, close) * 1.002, "low": np.minimum(open_, close) * 0.998,
                         "close": close, "volume": 1e6})


FRAMES = {"AAA": prices(1), "BBB": prices(2)}


def store_through(rows: int) -> pd.Timestamp:
    """Store the first ``rows`` bars of each test instrument, as an ingest up to that day would.

    Returns:
        The last stored session date.
    """
    for sym, frame in FRAMES.items():
        store.write_series("yahoo", sym, "1d", normalize_bars(frame.iloc[:rows]))
    return FRAMES["AAA"]["ts"].iloc[rows - 1]


def night(day: pd.Timestamp) -> datetime:
    """10 p.m. in New York on a session date (02:00 or 03:00 UTC the next day): after the close is final."""
    return (day + pd.Timedelta(hours=26)).to_pydatetime()


def save_card(card_id: str, meta: dict, verdict: str = "rejected") -> None:
    """Write a minimal report card with the fields the book reads."""
    pipeline._save_card({
        "card_id": card_id, "created_at": "2026-10-18T00:00:00", "verdict": verdict, "failed_stage": "permutation",
        "meta": {"lockbox_from": LOCKBOX, "research": [START, "2018-05-31"], **meta},
        "thresholds": {"version": 2},
        "stages": [{"key": "significance", "name": "Deflated Sharpe & PBO", "passed": None, "value": 0.7,
                    "threshold": "", "summary": "",
                    "chart": {"sr": 0.03, "skew": 0.0, "kurt": 3.0, "rho": 0.0, "n": 850, "sr_annual": 0.476}}],
    })


@pytest.fixture
def pair(monkeypatch):
    """A two-ETF universe with a trend-filter card, and an entry for it in the book."""
    monkeypatch.setitem(panel_mod.UNIVERSES, "test_pair",
                        {"members": [("yahoo", "AAA"), ("yahoo", "BBB")], "description": "test"})
    store_through(N_DAYS)
    save_card("card_pair", {"kind": "portfolio", "source": "panel", "symbol": "test_pair", "universe": "test_pair",
                            "interval": "1d", "question": "trend_filter:test_pair:1d", "family": "trend_filter",
                            "strategy": "trend_filter", "params": {"window": 60, "rebalance": 7}})
    return book.add("card_pair", note="test")


def test_session_times_follow_new_york_and_utc():
    """Equity bars close at 16:00 New York (final 30 minutes later); crypto bars close at the next UTC midnight."""
    winter, summer = pd.Timestamp("2026-01-16", tz="UTC"), pd.Timestamp("2026-07-17", tz="UTC")
    assert sessions.opens(winter, "equity")[0] == pd.Timestamp("2026-01-16 14:30", tz="UTC")
    assert sessions.opens(summer, "equity")[0] == pd.Timestamp("2026-07-17 13:30", tz="UTC")
    days = pd.DatetimeIndex([winter])
    assert not sessions.final_mask(days, ["equity"], datetime(2026, 1, 16, 21, 10, tzinfo=UTC))[0]
    assert sessions.final_mask(days, ["equity"], datetime(2026, 1, 16, 21, 31, tzinfo=UTC))[0]
    assert sessions.closes(winter, "crypto")[0] == pd.Timestamp("2026-01-17", tz="UTC")
    # A panel date is final only when its last member is.
    assert not sessions.final_mask(days, ["equity", "crypto"], datetime(2026, 1, 16, 23, 0, tzinfo=UTC))[0]
    assert sessions.next_session(winter, ["equity"]) == pd.Timestamp("2026-01-19", tz="UTC")  # Friday -> Monday
    assert sessions.next_session(winter, ["crypto"]) == pd.Timestamp("2026-01-17", tz="UTC")


def test_adding_a_card_freezes_expectations_without_spending_its_lockbox(pair):
    """The research re-run is a paper trial on its own question, outside the selection set and the lockbox."""
    ref = pair["reference"]
    assert pair["status"] == "active" and pair["verdict"] == "rejected" and pair["kind"] == "portfolio"
    assert ref["research"] == [START, "2018-05-31"] and ref["card"]["sr"] == 0.03
    assert np.isfinite(ref["beta"]) and ref["edge_sd"] > 0
    trials = ledger.list_trials(question="paper:trend_filter:test_pair:1d")
    assert [t["source"] for t in trials] == ["paper"] and trials[0]["trial_id"] == ref["trial_id"]
    assert not lockbox.spent("trend_filter:test_pair:1d")
    assert ledger.selection_set("test_pair", "1d") == []
    with pytest.raises(book.Refused):
        book.add("card_pair")


def test_hourly_cards_cannot_join():
    """The book decides once a day, so an hourly card is refused."""
    save_card("card_hourly", {"kind": "bars", "source": "binance", "symbol": "BTCUSDT", "interval": "1h",
                              "question": "trend_sma:BTCUSDT:1h", "family": "trend_sma", "strategy": "trend_sma",
                              "params": {}, "costs": COSTS})
    with pytest.raises(book.Refused):
        book.add("card_hourly")


def test_run_decides_the_latest_close_once_with_the_research_code(pair):
    """One decision per close, equal to the strategy's own weights on that date, filled at the next open."""
    last = FRAMES["AAA"]["ts"].iloc[N_DAYS - 1]
    first = book.run(now=night(last))
    assert [r["status"] for r in first] == ["decided"]
    r = first[0]
    assert r["as_of"] == str(last.date()) and not r["late"] and r["lead_hours"] > 0
    w = PORTFOLIOS["trend_filter"].weights(load_panel("test_pair"), {"window": 60, "rebalance": 7})[-1]
    assert [r["targets"]["AAA"], r["targets"]["BBB"]] == pytest.approx(list(w))
    assert book.run(now=night(last))[0]["status"] == "exists"
    assert len(book.decisions(pair["entry_id"])) == 1


def test_a_backfill_is_refused(pair):
    """A decision on a close whose next bar is already stored would be taken after its outcome."""
    last = FRAMES["AAA"]["ts"].iloc[N_DAYS - 1]
    earlier = FRAMES["AAA"]["ts"].iloc[N_DAYS - 2]
    res = book.decide(pair, now=night(last), as_of=str(earlier.date()))
    assert res["status"] == "refused" and "backfill" in res["message"]
    assert book.decisions(pair["entry_id"]) == []
    # A missed day stays missed: once two more bars are stored, only the newest close can be decided.
    book.run(now=night(last))
    newest = store_through(N_DAYS + 2)
    skipped = FRAMES["AAA"]["ts"].iloc[N_DAYS]
    assert book.decide(pair, now=night(newest), as_of=str(skipped.date()))["status"] == "refused"
    assert book.run(now=night(newest))[0]["as_of"] == str(newest.date())
    assert [d["as_of"] for d in book.decisions(pair["entry_id"])] == [str(last.date()), str(newest.date())]


def test_stale_data_is_refused(pair):
    """When the next session has closed but isn't stored, deciding now would be a backfill in disguise."""
    last = FRAMES["AAA"]["ts"].iloc[N_DAYS - 1]
    two_later = FRAMES["AAA"]["ts"].iloc[N_DAYS + 1]
    res = book.run(now=night(two_later))[0]
    assert res["status"] == "refused" and "stale" in res["message"]
    assert book.decisions(pair["entry_id"]) == []
    assert book.run(now=night(last))[0]["status"] == "decided"


def test_a_bar_still_trading_is_ignored_and_a_late_decision_is_flagged(pair):
    """During a session the stored bar isn't final: the book decides on yesterday's close and marks it late."""
    store_through(N_DAYS + 1)
    today = FRAMES["AAA"]["ts"].iloc[N_DAYS]
    res = book.run(now=(today + pd.Timedelta(hours=16)).to_pydatetime())[0]  # 11:00 or 12:00 in New York
    assert res["status"] == "decided" and res["as_of"] == str(FRAMES["AAA"]["ts"].iloc[N_DAYS - 1].date())
    assert res["late"] and res["lead_hours"] < 0


def test_scoring_replays_decisions_through_the_portfolio_engine(pair):
    """Forward returns equal the engine run with the strategy's own weights, from the bar after the first decision."""
    params = {"window": 60, "rebalance": 7}
    for k in range(12):
        day = store_through(N_DAYS + k)
        assert book.run(now=night(day))[0]["status"] == "decided"
    rep = score.score_entry(book.entry(pair["entry_id"]), now=night(day))
    assert rep["n_days"] == 11 and rep["through"] == str(day.date()) and rep["trial_id"]

    panel = load_panel("test_pair")
    window = np.zeros(len(panel.ts), dtype=bool)
    window[N_DAYS - 1:] = True
    w = PORTFOLIOS["trend_filter"].weights(panel, params)
    w[: N_DAYS - 1] = 0.0
    direct = run_portfolio(panel.slice(window), w[window])
    assert np.prod(1 + direct.net[1:]) - 1 == pytest.approx(rep["stats"]["strategy"]["total_return"])
    assert rep["series"]["equity"][-1] == pytest.approx(np.prod(1 + direct.net[1:]))
    assert len(rep["series"]["expected"]) == 11 and rep["band"]["status"] in ("inside", "below", "above")
    assert rep["alarm"]["applicable"] == (pair["reference"]["edge_mean"] > 0)

    # Ledger and score history get one row per scoring that saw new bars, and none otherwise.
    again = score.score_entry(book.entry(pair["entry_id"]), now=night(day))
    assert again["trial_id"] is None
    assert db.scalar("SELECT count(*) FROM paper_scores") == 1
    assert [t["source"] for t in ledger.list_trials(question="paper:trend_filter:test_pair:1d")] == ["paper"] * 2
    assert score.load_report(pair["entry_id"])["n_days"] == 11


def test_scoring_a_single_instrument_uses_the_bar_engine(monkeypatch):
    """A trend rule on one ETF is scored with the single-asset engine, positions filled at the next open."""
    store_through(N_DAYS)
    save_card("card_bars", {"kind": "bars", "source": "yahoo", "symbol": "AAA", "interval": "1d",
                            "question": "trend_sma:AAA:1d", "family": "trend_sma", "strategy": "trend_sma",
                            "params": {"window": 50, "entry_band": 0.0, "exit_band": 0.0}, "costs": COSTS})
    item = book.add("card_bars")
    for k in range(8):
        day = store_through(N_DAYS + k)
        book.run(now=night(day))
    rep = score.score_entry(book.entry(item["entry_id"]), now=night(day))
    assert rep["n_days"] == 7

    from qlab.backtest.strategies import REGISTRY

    bars = store.read_series("yahoo", "AAA", "1d").reset_index(drop=True)
    pos = REGISTRY["trend_sma"].positions(bars, {"window": 50, "entry_band": 0.0, "exit_band": 0.0})
    pos.iloc[: N_DAYS - 1] = 0.0
    part = bars.iloc[N_DAYS - 1:].reset_index(drop=True)
    direct = run_bars(part, pos.iloc[N_DAYS - 1:].reset_index(drop=True), CostModel(**COSTS))
    assert np.prod(1 + direct.returns.to_numpy()[1:]) - 1 == pytest.approx(rep["stats"]["strategy"]["total_return"])


def test_a_retired_entry_gets_no_decisions_and_stops_scoring(pair):
    """Retiring needs a reason; afterwards the run skips the entry and scoring ends at the retirement."""
    day = store_through(N_DAYS)
    book.run(now=night(day))
    with pytest.raises(book.Refused):
        book.retire(pair["entry_id"], " ")
    book.retire(pair["entry_id"], "test over", now=night(day))
    later = store_through(N_DAYS + 5)
    assert book.run(now=night(later)) == []
    rep = score.score_entry(book.entry(pair["entry_id"]), now=night(later))
    assert rep["n_days"] == 0 and rep["entry"]["status"] == "retired"


def test_bands_and_evidence_horizon():
    """The equity band grows with the square root of time; a 0.33 edge Sharpe needs decades to tell from zero."""
    eb = monitor.equity_band(400, 0.0004, 0.01)
    assert eb["lo"][0] < eb["mid"][0] < eb["hi"][0]
    width = np.log(eb["hi"] / eb["lo"])
    assert width[399] / width[99] == pytest.approx(2.0)
    years = monitor.evidence_days(0.33 / np.sqrt(252), 0.0, 3.0, 0.0) / 252
    assert 20 < years < 30
    floor, ceiling = monitor.sharpe_band(0.02, 252, 0.0, 3.0, 0.0)
    assert floor < 0.02 < ceiling


def test_cusum_false_alarms_and_detection_match_the_design():
    """In control the alarm fires about once per ARL0 days; when the edge vanishes it fires about ARL1 days in."""
    rng = np.random.default_rng(11)
    k, arl0 = 0.1, 300.0
    h = monitor.cusum_threshold(k, arl0)

    def mean_run(shift: float, paths: int = 3000, steps: int = 4000) -> float:
        s = np.zeros(paths)
        alarm_at = np.full(paths, steps, dtype=float)
        alive = np.ones(paths, dtype=bool)
        for t in range(steps):
            z = rng.standard_normal(paths) + shift
            s = np.maximum(0.0, s - z - k)
            hit = alive & (s > h)
            alarm_at[hit] = t + 1
            alive &= ~hit
            if not alive.any():
                break
        return float(alarm_at.mean())

    assert mean_run(0.0) == pytest.approx(arl0, rel=0.12)
    assert mean_run(-2 * k) == pytest.approx(monitor.siegmund_arl(k, h), rel=0.12)


def test_decay_alarm_needs_a_positive_research_edge():
    """Without a positive research edge there is nothing to decay."""
    ref = {"edge_mean": -0.0001, "edge_sd": 0.004, "dpy": 252}
    assert not monitor.decay_alarm(np.zeros(10), ref)["applicable"]
    ref["edge_mean"] = 0.0004
    res = monitor.decay_alarm(np.full(3000, -0.004), ref)
    assert res["applicable"] and res["alarm"] and res["first_alarm"] is not None


def test_paper_api(monkeypatch):
    """The Trading Desk's endpoints: the book, adding a card, the daily run and score, an entry and retiring it."""
    monkeypatch.setitem(panel_mod.UNIVERSES, "test_pair",
                        {"members": [("yahoo", "AAA"), ("yahoo", "BBB")], "description": "test"})
    store_through(N_DAYS)
    save_card("card_pair", {"kind": "portfolio", "source": "panel", "symbol": "test_pair", "universe": "test_pair",
                            "interval": "1d", "question": "trend_filter:test_pair:1d", "family": "trend_filter",
                            "strategy": "trend_filter", "params": {"window": 60, "rebalance": 7}})
    client = TestClient(create_app())
    assert [c["card_id"] for c in client.get("/api/paper/book").json()["eligible"]] == ["card_pair"]
    added = client.post("/api/paper/entries", json={"card_id": "card_pair", "note": "near-miss"}).json()
    assert client.post("/api/paper/entries", json={"card_id": "card_pair"}).status_code == 409
    assert client.post("/api/paper/entries", json={"card_id": "nope"}).status_code == 404
    book_state = client.get("/api/paper/book").json()
    assert book_state["eligible"] == [] and book_state["entries"][0]["entry_id"] == added["entry_id"]
    assert client.post("/api/paper/run").status_code == 200
    assert client.post("/api/paper/score").json()[0]["status"] == "scored"
    detail = client.get(f"/api/paper/entries/{added['entry_id']}").json()
    assert detail["entry"]["question"] == "trend_filter:test_pair:1d" and detail["report"] is not None
    assert client.get("/api/paper/entries/nope").status_code == 404
    assert client.post(f"/api/paper/entries/{added['entry_id']}/retire", json={"reason": ""}).status_code == 409
    assert client.post(f"/api/paper/entries/{added['entry_id']}/retire",
                       json={"reason": "done"}).json()["status"] == "retired"
