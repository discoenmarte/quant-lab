"""Tests for M6: the registry's lifecycle states and the near-miss rule, data freshness, the alert rules and channels
(and that a token never leaks), the daily report and its "since the last report" window, the API, and the nightly
job's report step."""

import json
import math
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from scipy.stats import norm

from qlab import alerts, config, db, ledger, registry, report
from qlab.api.main import create_app
from qlab.data import freshness, store
from qlab.data.bars import normalize_bars
from qlab.data.ingest import SourceInfo
from qlab.gauntlet import pipeline, stats


def stages(failed: tuple[str, ...] = (), p: float = 0.03, sr: float = 0.05) -> list[dict]:
    """Stage rows as a card stores them: every stage passes except ``failed``; bootstrap and lockbox can't reject.

    ``p`` is the permutation p-value and ``sr`` the daily edge Sharpe on the significance chart (2,000 research days,
    normal and uncorrelated: the default's undeflated PSR is 0.99, ``sr=0.01`` gives 0.67).
    """
    out = []
    for key, name in pipeline.STAGES:
        passed = None if key in ("bootstrap", "lockbox") else key not in failed
        value = {"costs": 0.4, "permutation": p, "significance": 0.8}.get(key)
        chart = {"sr": sr, "n": 2000, "skew": 0.0, "kurt": 3.0, "rho": 0.0} if key == "significance" else None
        out.append({"key": key, "name": name, "passed": passed, "value": value, "threshold": "", "summary": "",
                    "chart": chart})
    return out


def save_card(card_id: str, verdict: str = "rejected", failed: tuple[str, ...] = (),
              question: str | None = None, created: str = "2026-10-18T10:00:00", p: float = 0.03,
              sr: float = 0.05) -> None:
    """Write a minimal report card to the catalog (``p`` and ``sr`` as in ``stages``)."""
    question = question or f"rule_{card_id}:test_universe:1d"
    pipeline._save_card({
        "card_id": card_id, "created_at": created, "verdict": verdict, "failed_stage": failed[0] if failed else None,
        "meta": {"question": question, "family": question.split(":")[0], "strategy": question.split(":")[0],
                 "params": {}, "symbol": "test_universe", "lockbox_from": "2024-10-01"},
        "thresholds": {"version": 2}, "stages": stages(failed, p, sr),
    })


def add_entry(entry_id: str, card_id: str, status: str = "active") -> None:
    """Insert a paper-book entry row directly (the book itself is tested in test_paper)."""
    with db.connect() as con:
        con.execute("""INSERT INTO paper_entries (entry_id, card_id, kind, question, family, strategy, scope, params,
                                                  verdict, added_at, status, note, reference, retired_at)
                       VALUES (?, ?, 'portfolio', ?, 'f', 's', 'u', '{}', 'rejected', ?, ?, '', '{}', ?)""",
                    [entry_id, card_id, f"q_{card_id}", datetime(2026, 10, 1), status,
                     datetime(2026, 10, 10) if status == "retired" else None])


def bars(last: str, n: int = 30, freq: str = "B") -> pd.DataFrame:
    """Flat daily bars ending on ``last``."""
    ts = pd.date_range(end=pd.Timestamp(last, tz="UTC"), periods=n, freq=freq)
    return normalize_bars(pd.DataFrame({"ts": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                                        "volume": 1e6}))


@pytest.fixture
def sources(monkeypatch):
    """One small ETF source and one crypto source for freshness checks."""
    monkeypatch.setattr(freshness, "SOURCES", {
        "yahoo": SourceInfo("bars", "equity", "test", ["AAA", "BBB"]),
        "binance": SourceInfo("bars", "crypto", "test", ["XXXUSDT"]),
    })


# Registry


def test_near_miss_is_a_rejection_only_at_the_luck_stages_and_close_to_their_bar():
    """Failing only permutation or significance (or both), with p <= 0.10 and an undeflated PSR >= 0.80, is a
    near-miss; any other failure, none, or a miss beyond either cap is not."""
    ok = 0.9
    assert registry.is_near_miss("rejected", stages(("significance",)), ok)
    assert registry.is_near_miss("rejected", stages(("permutation", "significance")), ok)
    assert not registry.is_near_miss("rejected", stages(("walk_forward", "permutation")), ok)
    assert not registry.is_near_miss("rejected", stages(("lockbox",)), ok)
    assert not registry.is_near_miss("candidate", stages(), ok)
    assert not registry.is_near_miss("rejected", stages(), ok)
    assert registry.is_near_miss("rejected", stages(("permutation",), p=0.10), 0.80)  # both caps are inclusive
    assert not registry.is_near_miss("rejected", stages(("permutation", "significance"), p=0.293), ok)
    assert not registry.is_near_miss("rejected", stages(("significance",), p=0.012), 0.79)
    assert not registry.is_near_miss("rejected", stages(("significance",)), float("nan"))


def test_undeflated_psr_comes_from_the_significance_chart():
    """PSR with N = 1 against a Sharpe of zero, autocorrelation-adjusted; NaN when the chart is missing."""
    chart = {"sr": 0.02, "n": 2056, "skew": -0.3, "kurt": 6.0, "rho": 0.1}
    psr = registry.undeflated_psr(chart)
    assert psr == pytest.approx(stats.psr(0.02, 0.0, 2056, -0.3, 6.0, 0.1))
    assert psr < registry.undeflated_psr({**chart, "rho": 0.0})  # autocorrelated returns are less certain
    assert registry.undeflated_psr({**chart, "rho": 0.0, "skew": 0.0, "kurt": 3.0}) == pytest.approx(
        norm.cdf(0.02 * 2056**0.5))
    assert math.isnan(registry.undeflated_psr(None)) and math.isnan(registry.undeflated_psr({"sr": 0.02}))


def test_state_precedence():
    """An active paper entry wins, then a retirement, then the verdict; the near-miss rule applies to rejections."""
    luck = stages(("permutation",))
    assert registry.state_of("rejected", luck, in_book=True, retired=True, psr=0.9) == "in_paper_book"
    assert registry.state_of("validated", stages(), in_book=False, retired=True, psr=0.9) == "retired"
    assert registry.state_of("validated", stages(), in_book=False, retired=False, psr=0.9) == "validated"
    assert registry.state_of("candidate", stages(), in_book=False, retired=False, psr=0.9) == "candidate"
    assert registry.state_of("rejected", luck, in_book=False, retired=False, psr=0.9) == "near_miss"
    assert registry.state_of("rejected", luck, in_book=False, retired=False, psr=0.5) == "rejected"
    assert registry.state_of("rejected", stages(("costs",)), in_book=False, retired=False, psr=0.9) == "rejected"


def test_registry_derives_states_from_cards_the_book_and_retirements():
    """States come from the catalog, the paper book and the owner's retirements, and the verdict stays beside them."""
    save_card("c_cand", "candidate")
    save_card("c_near", "rejected", ("significance",))
    save_card("c_book", "rejected", ("permutation", "significance"))
    save_card("c_gone", "rejected", ("costs",))
    save_card("c_old", "rejected", ("walk_forward",))
    save_card("c_far", "rejected", ("permutation", "significance"), p=0.293)
    save_card("c_weak", "rejected", ("significance",), sr=0.01)
    add_entry("e_book", "c_book")
    add_entry("e_gone", "c_gone", status="retired")
    by = {c["card_id"]: c for c in registry.cards()}
    assert by["c_cand"]["state"] == "candidate" and by["c_near"]["state"] == "near_miss"
    assert by["c_book"]["state"] == "in_paper_book" and by["c_book"]["near_miss"]
    assert by["c_book"]["entry_id"] == "e_book"
    assert by["c_gone"]["state"] == "retired" and by["c_gone"]["since"].startswith("2026-10-10")
    assert by["c_old"]["state"] == "rejected" and by["c_old"]["failed"] == ["walk_forward"]
    assert by["c_near"]["edge"] == 0.4 and by["c_near"]["perm_p"] == 0.03
    assert by["c_near"]["psr"] == pytest.approx(norm.cdf(0.05 * 2000**0.5)) and by["c_old"]["psr"] is None
    assert by["c_far"]["state"] == "rejected" and not by["c_far"]["near_miss"] and by["c_far"]["psr"] > 0.8
    assert by["c_weak"]["state"] == "rejected" and by["c_weak"]["psr"] == pytest.approx(0.67, abs=0.01)
    assert registry.counts(list(by.values())) == {"candidate": 1, "validated": 0, "in_paper_book": 1, "near_miss": 1,
                                                  "rejected": 3, "retired": 1}


def test_retiring_needs_a_reason_and_stops_the_paper_entry(pair_entry):
    """Retiring records the reason, ends the card's active paper entry, and can't be done twice."""
    with pytest.raises(registry.Refused):
        registry.retire("card_pair", "  ")
    c = registry.retire("card_pair", "edge too small to ever confirm")
    assert c["state"] == "retired"
    from qlab.paper import book

    assert book.entry(pair_entry["entry_id"])["status"] == "retired"
    with pytest.raises(registry.Refused):
        registry.retire("card_pair", "again")
    with pytest.raises(KeyError):
        registry.retire("nope", "x")
    events = db.rows("SELECT * FROM audit WHERE action = 'registry.retire'")
    assert events and events[0]["reason"] == "edge too small to ever confirm"


@pytest.fixture
def pair_entry(monkeypatch):
    """A real paper-book entry for a two-ETF trend-filter card (as in test_paper)."""
    from test_paper import N_DAYS, store_through
    from test_paper import save_card as paper_card

    from qlab.paper import book
    from qlab.portfolio import panel as panel_mod

    monkeypatch.setitem(panel_mod.UNIVERSES, "test_pair",
                        {"members": [("yahoo", "AAA"), ("yahoo", "BBB")], "description": "test"})
    store_through(N_DAYS)
    paper_card("card_pair", {"kind": "portfolio", "source": "panel", "symbol": "test_pair", "universe": "test_pair",
                             "interval": "1d", "question": "trend_filter:test_pair:1d", "family": "trend_filter",
                             "strategy": "trend_filter", "params": {"window": 60, "rebalance": 7}})
    return book.add("card_pair", note="test")


# Data freshness


def test_expected_latest_session_follows_the_calendar():
    """ETFs: the last weekday whose close is final (16:30 New York); crypto: the last day closed at 00:00 UTC."""
    fri_night = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)  # 19:00 New York, Friday
    assert freshness.expected_latest("equity", "1d", fri_night) == pd.Timestamp("2026-10-16", tz="UTC")
    fri_afternoon = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)  # 16:00 New York: close not final yet
    assert freshness.expected_latest("equity", "1d", fri_afternoon) == pd.Timestamp("2026-10-15", tz="UTC")
    sunday = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    assert freshness.expected_latest("equity", "1d", sunday) == pd.Timestamp("2026-10-16", tz="UTC")
    assert freshness.expected_latest("crypto", "1d", sunday) == pd.Timestamp("2026-10-17", tz="UTC")
    assert freshness.expected_latest("crypto", "1h", sunday) == pd.Timestamp("2026-10-17 23:00", tz="UTC")
    assert freshness.expected_latest("factors", "1d", sunday) is None


def test_freshness_flags_the_stalest_symbol_beyond_its_tolerance(sources):
    """A source is as fresh as its stalest symbol; one session behind is tolerated for ETFs (holidays), two aren't."""
    store.write_series("yahoo", "AAA", "1d", bars("2026-10-16"))
    store.write_series("yahoo", "BBB", "1d", bars("2026-10-15"))
    store.write_series("binance", "XXXUSDT", "1d", bars("2026-10-16", freq="D"))
    monday_night = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)
    rows = {r["source"]: r for r in freshness.check(monday_night)}
    assert rows["yahoo"]["laggard"] == "BBB" and rows["yahoo"]["behind"] == 2 and rows["yahoo"]["stale"]
    assert rows["binance"]["behind"] == 2 and not rows["binance"]["stale"]
    friday_night = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)
    rows = {r["source"]: r for r in freshness.check(friday_night)}
    assert rows["yahoo"]["behind"] == 1 and not rows["yahoo"]["stale"]


def test_missing_series_are_stale(sources):
    """Nothing stored, or a default symbol missing, counts as stale."""
    store.write_series("yahoo", "AAA", "1d", bars("2026-10-16"))
    rows = {r["source"]: r for r in freshness.check(datetime(2026, 10, 16, 23, 0, tzinfo=UTC))}
    assert rows["yahoo"]["stale"] and rows["yahoo"]["missing"] == ["BBB"]
    assert rows["binance"]["stale"] and "nothing stored" in rows["binance"]["message"]


# Alerts


def fake_report(report_id="r1", cards=(), entries=(), fresh=()):
    """The parts of a report the alert rules read."""
    return {"report_id": report_id, "registry": {"cards": list(cards)}, "paper": {"entries": list(entries)},
            "freshness": list(fresh)}


def test_alerts_fire_on_changes_and_a_failed_nightly_every_time():
    """A condition alerts the first report that sees it, stays quiet while it lasts, and alerts again if it returns."""
    cand = {"card_id": "c1", "question": "q:1d", "verdict": "candidate", "edge": 0.5, "perm_p": 0.01, "dsr": 0.97}
    entry = {"entry_id": "e1", "question": "p:1d", "status": "active",
             "alarm": {"alarm": True, "value": 50.0, "h": 41.6}}
    stale = {"source": "yahoo", "interval": "1d", "stale": True, "message": "yahoo 1d: 3 sessions behind."}
    rep = fake_report(cards=[cand], entries=[entry], fresh=[stale])
    first = alerts.evaluate(rep, None, None)
    assert {a.rule for a in first} == {"new_candidate", "paper_alarm", "stale_data"}
    seen = set(alerts.conditions(rep))
    assert alerts.evaluate(rep, seen, None) == []
    cleared = fake_report(cards=[cand], entries=[entry])
    assert alerts.evaluate(cleared, seen, None) == []
    back = alerts.evaluate(rep, set(alerts.conditions(cleared)), None)
    assert [a.rule for a in back] == ["stale_data"]
    failed = {"ok": False, "mode": "local", "ingest": [{"source": "yahoo", "status": "error", "errors": ["HTTP 503"]}],
              "run": {"messages": ["stale data: ingest first"]}}
    for _ in range(2):
        out = alerts.evaluate(rep, seen, failed)
        assert [a.rule for a in out] == ["nightly_failed"] and "HTTP 503" in out[0].message


def test_retired_entries_and_rejected_cards_raise_nothing():
    """Only active entries can alarm, and only candidate or validated verdicts count as new candidates."""
    rep = fake_report(cards=[{"card_id": "c", "question": "q", "verdict": "rejected"}],
                      entries=[{"entry_id": "e", "question": "q", "status": "retired", "alarm": {"alarm": True}}])
    assert alerts.evaluate(rep, None, None) == []


def test_delivery_survives_a_failing_channel(monkeypatch):
    """One broken channel is recorded and the others still deliver; delivery never raises."""
    class Broken:
        name = "broken"

        def send(self, alert):
            raise RuntimeError("down")

    monkeypatch.setattr(alerts, "channels", lambda notify=True: [Broken(), alerts.FileChannel()])
    res = alerts.deliver([alerts.Alert("test", "info", "k", "t", "m")])
    assert res == [{"broken": "error: down", "file": "ok"}]
    line = (config.data_dir() / "alerts" / "alerts.jsonl").read_text().splitlines()[-1]
    assert json.loads(line)["key"] == "k"


def test_telegram_errors_never_contain_the_token(monkeypatch):
    """httpx errors carry the URL (which holds the token); the channel reports the error type only."""
    token = "123456:SECRET-token"
    ch = alerts.TelegramChannel(token, "42")

    def connect_fails(url, **kw):
        raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(alerts.httpx, "post", connect_fails)
    with pytest.raises(RuntimeError) as err:
        ch.send(alerts.Alert("test", "info", "k", "t", "m"))
    assert token not in str(err.value) and err.value.__cause__ is None and err.value.__suppress_context__

    monkeypatch.setattr(alerts.httpx, "post", lambda url, **kw: httpx.Response(401, text=f"bad token {token}"))
    with pytest.raises(RuntimeError) as err:
        ch.send(alerts.Alert("test", "info", "k", "t", "m"))
    assert token not in str(err.value) and "401" in str(err.value)

    sent = {}
    monkeypatch.setattr(alerts.httpx, "post", lambda url, **kw: sent.update(url=url, **kw) or httpx.Response(200))
    ch.send(alerts.Alert("paper_alarm", "bad", "k", "Decay alarm", "details"))
    assert sent["json"]["chat_id"] == "42" and "Decay alarm" in sent["json"]["text"]


def test_channels_come_from_settings_and_lab_env(monkeypatch):
    """Telegram turns on only with both settings, which may live in the data folder's lab.env; status hides them."""
    monkeypatch.delenv("QLAB_ALERT_CHANNELS")
    monkeypatch.setattr(alerts, "sys", SimpleNamespace(platform="linux"))
    assert alerts.channel_names() == ["file"]
    config.ensure_dirs()
    config.env_file().write_text('# secrets\nexport QLAB_TELEGRAM_TOKEN="999:abc"\nQLAB_TELEGRAM_CHAT=4242\n')
    assert config.setting("QLAB_TELEGRAM_TOKEN") == "999:abc"
    assert alerts.channel_names() == ["file", "telegram"]
    assert [c.name for c in alerts.channels(notify=False)] == ["file"]
    status = json.dumps(alerts.channel_status())
    assert "999:abc" not in status and "4242" not in status
    monkeypatch.setenv("QLAB_ALERT_CHANNELS", "file")
    assert alerts.channel_names() == ["file"]


def test_macos_text_is_passed_as_arguments(monkeypatch):
    """The notification text goes to osascript as arguments, so quotes in it can't change the script."""
    calls = []
    monkeypatch.setattr(alerts, "shutil", SimpleNamespace(which=lambda name: "/usr/bin/osascript"))
    monkeypatch.setattr(alerts, "subprocess", SimpleNamespace(
        run=lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0, stderr="")))
    alerts.MacOSChannel().send(alerts.Alert("test", "info", "k", 'say "hi"', 'quote " and \\ backslash'))
    args = calls[0]
    assert args[-2] == 'quant-lab: say "hi"' and args[-1] == 'quote " and \\ backslash'
    assert all('"hi"' not in a for a in args[:-2])


# The daily report


def record(question: str, source: str = "gauntlet", n: int = 1, edge: float = 0.3) -> None:
    """Record ``n`` trials on a question."""
    ledger.record_trials([dict(question=question, family="f", strategy="s", params={"i": i}, symbol="X",
                               start="2020-01-01", end="2023-12-31", source=source,
                               metrics={"sharpe": 0.5, "edge_sharpe": edge, "n_obs": 900}) for i in range(n)])


def test_report_covers_the_time_since_the_previous_one_and_adds_no_trial(sources):
    """Each report counts what happened since the last; building one records no trial."""
    store.write_series("yahoo", "AAA", "1d", bars("2026-10-16"))
    store.write_series("yahoo", "BBB", "1d", bars("2026-10-16"))
    store.write_series("binance", "XXXUSDT", "1d", bars("2026-10-17", freq="D"))
    record("trend:X:1d", n=5)
    record("calib:noise:1", source="calibration", n=40)
    save_card("c_near", "rejected", ("significance",), question="trend:X:1d")
    n_before = ledger.count_trials()
    first = report.build()
    assert ledger.count_trials() == n_before
    assert first["since"] is None and first["trials"]["research"] == 5 and first["trials"]["calibration"] == 40
    assert first["trials"]["by_question"][0] == {"question": "trend:X:1d", "source": "gauntlet", "n": 5,
                                                 "best_edge_sharpe": 0.3, "n_total": 5}
    assert [c["card_id"] for c in first["verdicts"]] == ["c_near"]
    assert any("near-miss: p 0.030, PSR 0.99" in h["text"] for h in first["headline"] if h["topic"] == "verdicts")

    record("trend:X:1d", n=2)
    second = report.build()
    assert second["since"] is not None and second["previous_id"] == first["report_id"]
    assert second["trials"]["research"] == 2 and second["trials"]["by_question"][0]["n_total"] == 7
    assert second["verdicts"] == [] and second["transitions"] == []
    assert report.latest()["report_id"] == second["report_id"]
    assert [r["report_id"] for r in report.list_reports()] == [second["report_id"], first["report_id"]]
    assert report.load(first["report_id"])["headline"] == first["headline"]
    with pytest.raises(FileNotFoundError):
        report.load("nope")


def test_report_alerts_once_per_condition_and_records_transitions(sources):
    """Stale data alerts in the first report only; a retirement shows up as a move; a failed nightly alerts."""
    save_card("c_cand", "candidate")
    first = report.build()  # nothing stored: both sources stale
    assert sorted(a["rule"] for a in first["alerts"]) == ["new_candidate", "stale_data", "stale_data"]
    assert all(a["delivery"] == {"file": "ok"} for a in first["alerts"])
    registry.retire("c_cand", "not pursuing it")
    nightly = {"ok": False, "mode": "local", "ingest": [], "run": {"messages": ["stale data: ingest first"]}}
    second = report.build(trigger="nightly", nightly=nightly, send_digest=True)
    assert [a["rule"] for a in second["alerts"]] == ["nightly_failed", "daily_report"]
    assert second["transitions"] == [{"card_id": "c_cand", "question": "rule_c_cand:test_universe:1d",
                                      "from": "candidate", "to": "retired"}]
    assert second["nightly"]["last"]["ok"] is False
    assert any(h["topic"] == "nightly" and h["tone"] == "bad" for h in second["headline"])
    assert len(alerts.recent()) == 5


def test_report_reads_the_paper_book(pair_entry):
    """The paper section comes from the book's saved forward reports and counts the decisions taken since."""
    from test_paper import FRAMES, night, store_through

    from qlab.paper import book, score

    day = store_through(len(FRAMES["AAA"]) - 25)
    book.run(now=night(day))
    for k in range(1, 4):
        day = store_through(len(FRAMES["AAA"]) - 25 + k)
        book.run(now=night(day))
    score.score(now=night(day))
    rep = report.build(now=night(day))
    e = rep["paper"]["entries"][0]
    assert e["entry_id"] == pair_entry["entry_id"] and e["n_days"] == 3 and e["new_decisions"] == 4
    assert e["band"]["status"] in ("inside", "below", "above") and e["alarm"]["applicable"] in (True, False)
    assert rep["paper"]["runs"]["decided"] == 4
    assert any(h["topic"] == "paper_entry" and "over 3 days" in h["text"] for h in rep["headline"])


# API


def test_registry_report_and_alert_api(monkeypatch):
    """The board's endpoints: registry, retire, build and read reports, the alert log, channels and a test alert."""
    monkeypatch.setenv("QLAB_TELEGRAM_TOKEN", "777:never-shown")
    save_card("c_near", "rejected", ("permutation",))
    client = TestClient(create_app())
    reg = client.get("/api/registry").json()
    assert reg["counts"]["near_miss"] == 1 and [s["key"] for s in reg["states"]] == registry.STATES
    assert client.post("/api/registry/c_near/retire", json={"reason": ""}).status_code == 409
    assert client.post("/api/registry/nope/retire", json={"reason": "x"}).status_code == 404
    assert client.post("/api/registry/c_near/retire", json={"reason": "done"}).json()["state"] == "retired"

    assert client.get("/api/report/latest").json() is None
    assert client.post("/api/report", json={"trigger": "bogus"}).status_code == 422
    made = client.post("/api/report", json={"trigger": "web", "notify": False}).json()
    assert made["trigger"] == "web" and made["registry"]["counts"]["retired"] == 1
    assert client.get("/api/report/latest").json()["report_id"] == made["report_id"]
    assert client.get(f"/api/report/{made['report_id']}").status_code == 200
    assert client.get("/api/report/nope").status_code == 404
    assert client.get("/api/reports").json()[0]["report_id"] == made["report_id"]
    assert "never-shown" not in client.get("/api/alerts/channels").text
    sent = client.post("/api/alerts/test").json()
    assert sent["rule"] == "test" and sent["delivery"] == {"file": "ok"}
    assert client.get("/api/alerts").json()[0]["alert_id"] == sent["alert_id"]


# The nightly job's report step


def test_nightly_crash_still_reports_and_alerts(monkeypatch):
    """A step that crashes ends in a report with a failed-nightly alert, and the job reports failure."""
    from qlab.paper import nightly

    monkeypatch.setattr(nightly, "api_up", lambda api: False)

    def crash(log):
        raise ConnectionError("network down")

    monkeypatch.setattr(nightly, "_in_process", crash)
    res = nightly.nightly(log=lambda msg: None)
    assert res["ok"] is False and "network down" in res["error"]
    rules = [a["rule"] for a in res["report"]["alerts"]]
    assert "nightly_failed" in rules and "daily_report" in rules
    stored = report.latest()
    assert stored["nightly"]["this_run"]["error"].endswith("network down")


def test_nightly_alerts_without_the_database_when_the_report_fails(monkeypatch):
    """If the report can't be built, the failure alert still reaches the file channel, and the job fails."""
    from qlab.paper import nightly

    monkeypatch.setattr(nightly, "api_up", lambda api: False)
    monkeypatch.setattr(nightly, "_in_process", lambda log: {"ingest": [], "run": [], "score": []})

    def broken(**kw):
        raise RuntimeError("database locked")

    monkeypatch.setattr(report, "build", broken)
    res = nightly.nightly(log=lambda msg: None)
    assert res["ok"] is False and res["report"] == {"error": "RuntimeError: database locked"}
    line = json.loads((config.data_dir() / "alerts" / "alerts.jsonl").read_text().splitlines()[-1])
    assert line["rule"] == "nightly_failed" and "database locked" in line["message"]


def test_nightly_summary_is_compact():
    """The summary keeps counts and the first messages, not every result."""
    from qlab.paper import nightly

    res = {"mode": "local", "ok": False,
           "ingest": [{"source": "yahoo", "status": "error", "n_series": 0, "errors": list(range(10))}],
           "run": [{"status": "refused", "message": f"m{i}"} for i in range(8)],
           "score": [{"entry_id": "e", "status": "scored", "alarm": True}]}
    t = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)
    s = nightly.summary(res, t, t)
    assert s["ingest"][0]["errors"] == ["0", "1", "2"] and s["run"]["refused"] == 8 and len(s["run"]["messages"]) == 5
    assert s["score"] == {"scored": 1, "error": 0, "alarms": ["e"]}
    json.dumps(s)  # it goes into the audit log and an HTTP body
