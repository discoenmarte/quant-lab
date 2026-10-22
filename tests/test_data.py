"""Tests for M1: source parsers, quality checks, storage, ingestion and the data API. No network access."""

import io
import json
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qlab import audit
from qlab.api.main import create_app
from qlab.data import ingest, quality, store
from qlab.data.bars import adjusted, normalize_bars
from qlab.data.sources import binance, french, yahoo


def _zip(name: str, text: str) -> bytes:
    """Wrap text in a one-file zip, like the Binance archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


def test_binance_reads_milliseconds_and_microseconds():
    """Rows before 2025 are in milliseconds, rows from 2025 in microseconds; both map to the right date."""
    csv = (
        "1733011200000,96407.99,97836.0,95693.88,97185.18,16938.6,1733097599999,1.6e9,3342200,8114.8,7.8e8,0\n"
        "1735689600000000,93576.0,95151.15,92888.0,94591.79,10373.3,1735775999999999,9.7e8,1516556,5347.7,5.0e8,0\n"
    )
    bars = binance.parse_klines(_zip("x.csv", csv))
    assert list(bars["ts"].dt.strftime("%Y-%m-%d")) == ["2024-12-01", "2025-01-01"]
    assert bars["close"].tolist() == [97185.18, 94591.79]


def test_binance_skips_a_header_row():
    """Some archive files start with column names."""
    csv = "open_time,open,high,low,close,volume,close_time,qv,trades,tbb,tbq,ignore\n" \
          "1733011200000,1,2,0.5,1.5,10,1733097599999,15,3,5,7,0\n"
    assert len(binance.parse_klines(csv.encode())) == 1


def test_yahoo_keys_bars_by_session_date_and_reads_events():
    """Open-of-session timestamps become the session date at 00:00 UTC; dividends are parsed."""
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": [1704205800, 1704292200],  # 2024-01-02 and 2024-01-03, 09:30 New York
                    "indicators": {
                        "quote": [{"open": [100, 101], "high": [102, 103], "low": [99, 100],
                                   "close": [101, 102], "volume": [1000, 1100]}],
                        "adjclose": [{"adjclose": [99.0, 100.0]}],
                    },
                    "events": {"dividends": {"1704292200": {"amount": 1.5, "date": 1704292200}}},
                }
            ],
            "error": None,
        }
    }
    bars, events = yahoo.parse_chart(json.dumps(payload))
    assert list(bars["ts"].dt.strftime("%Y-%m-%d %H:%M")) == ["2024-01-02 00:00", "2024-01-03 00:00"]
    assert bars["adj_close"].tolist() == [99.0, 100.0]
    assert events.iloc[0]["kind"] == "dividend" and events.iloc[0]["value"] == 1.5


def test_yahoo_tops_up_a_lagging_history_on_its_dividend_basis():
    """Sessions only the short query has are appended, rescaled so adjusted returns stay right across an ex-date."""
    days = pd.to_datetime(["2026-10-19", "2026-10-20", "2026-10-21"], utc=True)
    full = pd.DataFrame({"ts": days[:2], "open": [100.0, 101.0], "high": [101.0, 102.0], "low": [99.0, 100.0],
                         "close": [100.0, 101.0], "adj_close": [100.0, 101.0], "volume": [1.0, 1.0]})
    # A 1.0 dividend went ex on 2026-10-21: the short query's earlier adjusted closes sit lower by 100/101.
    k = 1 - 1.0 / 101.0
    recent = pd.DataFrame({"ts": days, "open": [100.0, 101.0, 100.5], "high": [101.0, 102.0, 101.0],
                           "low": [99.0, 100.0, 99.5], "close": [100.0, 101.0, 100.5],
                           "adj_close": [100.0 * k, 101.0 * k, 100.5], "volume": [1.0, 1.0, 1.0]})
    ev = pd.DataFrame({"ts": days[2:], "kind": ["dividend"], "value": [1.0]})
    empty = pd.DataFrame(columns=["ts", "kind", "value"])
    bars, events = yahoo.merge_recent(full, empty, recent, ev)
    assert list(bars["ts"].dt.strftime("%Y-%m-%d")) == ["2026-10-19", "2026-10-20", "2026-10-21"]
    assert bars["adj_close"].iloc[2] / bars["adj_close"].iloc[1] == pytest.approx(100.5 / (101.0 * k))
    assert bars["close"].iloc[2] == 100.5 and events["kind"].tolist() == ["dividend"]
    same, _ = yahoo.merge_recent(full, empty, recent.iloc[:2], empty)
    assert len(same) == 2
    disjoint, _ = yahoo.merge_recent(full, empty, recent.iloc[2:], ev)
    assert len(disjoint) == 2  # no shared session to put the new bar on the history's basis


def test_french_parses_percent_into_fractions():
    """Preamble, header, rows, then a blank line and the copyright notice."""
    text = (
        "This file was created by using the CRSP database.\n\n"
        ",Mkt-RF,SMB,HML,RF\n"
        "19260701,    0.09,   -0.23,   -0.28,    0.01\n"
        "19260702,    0.45,   -0.34,   -0.03,    0.01\n\n"
        "Copyright 2026 Eugene F. Fama and Kenneth R. French\n"
    )
    df = french.parse_factors(text)
    assert len(df) == 2
    assert df["rf"].iloc[0] == pytest.approx(0.0001)
    assert df["mkt_rf"].iloc[1] == pytest.approx(0.0045)


def _bars(n: int = 10) -> pd.DataFrame:
    """Clean synthetic daily bars."""
    ts = pd.bdate_range("2024-01-01", periods=n, tz="UTC")
    close = pd.Series(range(100, 100 + n), dtype=float)
    return normalize_bars(pd.DataFrame({"ts": ts, "open": close, "high": close + 1, "low": close - 1,
                                        "close": close, "volume": 1000.0}))


def test_quality_flags_injected_problems():
    """Each injected defect is caught by the matching check, with the right severity."""
    df = _bars(30)
    df.loc[3, "close"] = -1.0          # non-positive price
    df.loc[6, "low"] = df.loc[6, "high"] + 5  # impossible bar
    df.loc[10, ["open", "high", "low", "close"]] = [300, 301, 299, 300]  # +170% move
    df = df.drop(index=range(15, 22)).reset_index(drop=True)  # 7 missing weekdays
    found = {(i.check, i.severity) for i in quality.check_bars(df, asset_class="equity", interval="1d")}
    assert ("non_positive_price", "error") in found
    assert ("ohlc_inconsistent", "error") in found
    assert ("extreme_return", "warn") in found
    assert ("gap", "warn") in found


def test_clean_bars_pass_quality():
    """Ordinary bars produce no issues."""
    assert quality.check_bars(_bars(30), asset_class="equity", interval="1d") == []


def test_adjusted_scales_ohlc_by_the_adjustment_factor():
    """A 2% dividend adjustment scales open, high and low like the close."""
    df = _bars(2).assign(adj_close=lambda d: d["close"] * 0.98)
    adj = adjusted(df)
    assert adj["open"].tolist() == pytest.approx((df["open"] * 0.98).tolist())
    assert adj["close"].tolist() == pytest.approx(df["adj_close"].tolist())


def test_store_roundtrip_and_catalog():
    """A written series reads back identically and appears in the catalog with its issue counts."""
    df = _bars(5)
    entry = store.write_series("test", "XYZ", "1d", df)
    assert entry["n_rows"] == 5
    pd.testing.assert_frame_equal(store.read_series("test", "XYZ", "1d"), df)
    quality.record_issues("test", "XYZ", "1d", [quality.Issue("gap", "warn", None, "x")])
    row = next(r for r in store.list_series() if r["symbol"] == "XYZ")
    assert row["n_warnings"] == 1 and row["n_errors"] == 0


def test_ingest_updates_catalog_status_and_audit(monkeypatch):
    """Ingest stores each interval, sets the source status and writes an audit event."""
    monkeypatch.setattr(binance, "fetch", lambda symbol, interval: _bars(20))
    res = ingest.ingest("binance", ["BTCUSDT"])
    assert res["status"] == "ok" and len(res["series"]) == 2  # 1d and 1h
    src = next(s for s in ingest.list_sources() if s["name"] == "binance")
    assert src["status"] == "ok" and src["n_series"] == 2
    assert audit.recent(1)[0]["action"] == "data.ingest"


def test_ingest_keeps_going_when_one_symbol_fails(monkeypatch):
    """A failing symbol is reported, the others are stored, and the status is a warning."""
    def fake(symbol):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return _bars(10), pd.DataFrame(columns=["ts", "kind", "value"])

    monkeypatch.setattr(yahoo, "fetch", fake)
    res = ingest.ingest("yahoo", ["SPY", "BAD"])
    assert res["status"] == "warn"
    assert len(res["series"]) == 1 and "BAD" in res["errors"][0]


def test_data_api_lists_sources_and_serves_bars():
    """Sources exist before any ingest; stored bars come back with Unix-second times."""
    client = TestClient(create_app())
    names = {s["name"] for s in client.get("/api/data/sources").json()}
    assert names == {"binance", "yahoo", "french"}
    store.write_series("yahoo", "SPY", "1d", _bars(3).assign(adj_close=lambda d: d["close"]))
    body = client.get("/api/data/bars", params={"source": "yahoo", "symbol": "SPY", "adjusted": True}).json()
    assert body["n_total"] == 3 and body["adjusted"] is True
    assert body["bars"][0]["time"] == int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
    assert client.get("/api/data/bars", params={"source": "yahoo", "symbol": "NOPE"}).status_code == 404
