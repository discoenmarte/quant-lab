"""Tests for the scenario board and the radar's read-only overview."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import numpy as np
import pandas as pd

from qlab.radar import overview, scan, scenarios, store, watchlist

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def put_market(name: str, probs: list[float], label: str) -> None:
    """Store daily snapshots of one market ending today."""
    ts = pd.date_range(end="2026-10-22", periods=len(probs), freq="D", tz="UTC")
    df = pd.DataFrame({"ts": ts, "prob": probs, "bid": np.nan, "ask": np.nan, "published_at": ts, "fetched_at": ts})
    store.upsert("markets", name, df, {"label": label, "category": "scenario", "unit": "probability"})


def test_the_board_groups_markets_by_question_with_changes_and_base_rates(isolated_lab):
    """Each curated question lists its stored markets, most likely first, with 1, 7 and 30-day changes."""
    watchlist.path().parent.mkdir(parents=True)
    watchlist.path().write_text(json.dumps({"scenarios": [
        {"id": "rec", "title": "US recession by end of 2026", "category": "macro", "source": "polymarket",
         "event": "us-recession", "base_rate": "about 16% a year"},
        {"id": "empty", "title": "Nothing stored yet", "source": "kalshi", "ticker": "KX-1", "series": "KX"}]}))
    put_market("rec__a", [0.10] * 30 + [0.12, 0.30], "Recession: yes")
    put_market("rec__b", [0.50] * 5, "Recession: other")
    b = scenarios.board(NOW)
    assert "never a trading signal" in b["note"]
    rec, empty = b["scenarios"]
    assert [m["name"] for m in rec["markets"]] == ["rec__b", "rec__a"]
    a = rec["markets"][1]
    assert a["prob"] == 0.30 and round(a["change_1d"], 2) == 0.18 and round(a["change_7d"], 2) == 0.20
    assert rec["markets"][0]["change_30d"] is None and rec["base_rate"] == "about 16% a year"
    assert rec["link"] == "https://polymarket.com/event/us-recession" and empty["markets"] == []
    assert empty["link"] == "https://kalshi.com/markets/kx"


def test_search_lists_open_events_most_traded_first(isolated_lab):
    """The owner's helper for finding new scenario slugs."""
    body = {"events": [
        {"slug": "small", "title": "Small", "volume": 10, "markets": [{"slug": "s", "outcomePrices": '["0.2","0.8"]'}]},
        {"slug": "big", "title": "Big", "volume": 999, "endDate": "2027-01-01T00:00:00Z",
         "markets": [{"slug": "b", "groupItemTitle": "Yes", "outcomePrices": '["0.7","0.3"]'},
                     {"slug": "c", "closed": True}]},
        {"slug": "done", "closed": True}]}
    f = store.Fetcher(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))),
                      sleep=lambda _s: None)
    out = scenarios.search("recession", f)
    assert [e["slug"] for e in out] == ["big", "small"]
    assert out[0]["markets"] == [{"slug": "b", "label": "Yes", "prob": 0.7}] and out[0]["end"] == "2027-01-01"


def test_the_overview_reads_without_fetching_and_a_signal_view_has_its_thresholds(isolated_lab):
    """Before anything is collected the overview is empty but valid; after a scan it carries signals and charts."""
    empty = overview.overview(NOW)
    assert empty["scan"] is None and empty["anomalies"] == [] and "risk-and-awareness" in empty["note"]
    watchlist.path().parent.mkdir(parents=True, exist_ok=True)
    watchlist.path().write_text(json.dumps({"wiki": [{"article": "Bank_run", "label": "Wikipedia: Bank run",
                                                      "category": "macro"}], "hn": [], "gdelt": [], "scenarios": [],
                                            "prices": {}}))
    ts = pd.date_range(end="2026-10-21", periods=400, freq="D", tz="UTC")
    views = 1000 * np.exp(np.random.default_rng(1).normal(0, 0.1, 400))
    store.upsert("wiki", "bank_run", pd.DataFrame({"ts": ts, "views": views, "published_at": ts, "fetched_at": ts}),
                 {"label": "Wikipedia: Bank run", "category": "macro"})
    scan.run(NOW, notify=False)
    o = overview.overview(NOW)
    assert o["scan"]["n_signals"] == 1 and o["signals"][0]["signal"] == "wiki/bank_run"
    assert set(o["signals"][0]["detectors"]) == {"robust_z", "cusum", "bocpd"}
    v = overview.signal_view("wiki/bank_run")
    assert v["points"][-1]["value"] == views[-1] and set(v["thresholds"]) == {"robust_z", "cusum", "bocpd"}
    assert v["thresholds"]["robust_z"]["severe"] > v["thresholds"]["robust_z"]["notable"]


def test_the_api_serves_the_radar_and_refuses_unknown_passes_and_signals(isolated_lab):
    """Reads answer before anything is collected; bad input is a clear 4xx."""
    from fastapi.testclient import TestClient

    from qlab.api.main import create_app

    client = TestClient(create_app())
    assert client.get("/api/radar").json()["scan"] is None
    assert client.get("/api/radar/scenarios").status_code == 200
    assert client.get("/api/radar/anomalies", params={"status": "all"}).json() == []
    assert client.get("/api/radar/calibration").json() is None
    assert client.get("/api/radar/signal", params={"key": "wiki/nothing"}).status_code == 404
    assert client.post("/api/radar/collect", json={"kind": "hourly"}).status_code == 400
    assert client.post("/api/radar/run", json={"kind": "quick", "only": ["metaculus"]}).status_code == 400
    out = client.post("/api/radar/scan", json={}).json()
    assert out["n_signals"] == 0 and "readings" not in out


def test_the_server_runs_the_radar_through_the_api_on_its_own_timers():
    """Each radar timer has its service, both call `qlab radar run`, deploy enables them, and the heartbeat is apart."""
    from qlab import config

    units = config.ROOT / "ops" / "server" / "systemd"
    deploy = (config.ROOT / "ops" / "server" / "deploy.sh").read_text()
    for name, cmd in (("qlab-radar", "qlab radar run\n"), ("qlab-radar-deep", "qlab radar run --deep\n")):
        service = (units / f"{name}.service").read_text()
        assert (units / f"{name}.timer").exists() and cmd in service and f"{name}.timer" in deploy
        assert "heartbeat" not in service
    assert "OnFailure" not in (units / "qlab-radar.service").read_text()  # no alert storm every 15 minutes
    assert "OnFailure" in (units / "qlab-radar-deep.service").read_text()
    assert "*:0/15" in (units / "qlab-radar.timer").read_text()
