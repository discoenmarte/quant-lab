"""Tests for Pipeline C (M5b): the SEC insider data, the aggregate insider rule and its point-in-time alignment."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest

from qlab import lockbox
from qlab.backtest.strategies import REGISTRY
from qlab.data import store
from qlab.data.bars import normalize_bars
from qlab.gauntlet.subjects import BarSubject
from qlab.informed import insiders


def quarter_zip(sub_rows: list[str], trans_rows: list[str]) -> bytes:
    """A quarter's zip with the two tables the parser reads."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\tOTHER\n"
                   + "\n".join(sub_rows) + "\n")
        z.writestr("NONDERIV_TRANS.tsv", "ACCESSION_NUMBER\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tX\n"
                   + "\n".join(trans_rows) + "\n")
    return buf.getvalue()


def test_quarters_stop_at_the_last_finished_one():
    """The running quarter's file doesn't exist yet."""
    q = insiders.quarters(date(2026, 10, 22))
    assert q[0] == "2006q1" and q[-1] == "2026q3" and len(q) == 83
    assert insiders.quarters(date(2026, 2, 1))[-1] == "2025q4"


def test_a_quarter_becomes_daily_buying_and_selling_by_filing_date():
    """Original Form 4 purchases and sales count once per filing and side; amendments and grants are left out."""
    payload = quarter_zip(
        ["A1\t02-JAN-2024\t4\t100\tx", "A2\t02-JAN-2024\t4\t200\tx", "A3\t03-JAN-2024\t4/A\t100\tx",
         "A4\t03-JAN-2024\t4\t300\tx"],
        ["A1\tP\t100\t10\tx", "A1\tP\t50\t10\tx", "A2\tS\t10\t20\tx", "A3\tP\t1\t1\tx", "A4\tA\t5\t0\tx",
         "A4\tS\t1\t5\tx"])
    df = insiders.parse_quarter(payload)
    first = df[df["ts"] == pd.Timestamp("2024-01-02", tz="UTC")].iloc[0]
    assert first["buy_filings"] == 1 and first["sell_filings"] == 1 and first["buy_usd"] == 1500
    second = df[df["ts"] == pd.Timestamp("2024-01-03", tz="UTC")].iloc[0]
    assert second["buy_filings"] == 0 and second["sell_filings"] == 1 and second["sell_firms"] == 1


def test_downloads_need_a_contact_address_are_cached_and_say_why_they_fail(monkeypatch):
    """No contact address, no request; a file is fetched once; 404 means not published; 403 is an error."""
    calls: list[str] = []
    body = quarter_zip(["A1\t02-JAN-2024\t4\t100\tx"], ["A1\tP\t1\t1\tx"])

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert "@" in request.headers["user-agent"]
        q = str(request.url).rsplit("/", 1)[-1][:6]
        return {"2024q1": httpx.Response(200, content=body), "2024q2": httpx.Response(404)}.get(
            q, httpx.Response(403))

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(insiders, "PAUSE_SECONDS", 0)
    monkeypatch.setenv("QLAB_SEC_USER_AGENT", "")
    with pytest.raises(insiders.SECError, match="contact address"):
        insiders.download("2024q1", client)
    monkeypatch.setenv("QLAB_SEC_USER_AGENT", "quant-lab test owner@example.com")
    assert insiders.download("2024q1", client).exists() and insiders.download("2024q1", client).exists()
    assert len(calls) == 1
    assert insiders.download("2024q2", client) is None
    with pytest.raises(insiders.SECError, match="403"):
        insiders.download("2024q3", client)


def daily_series(start: str = "2006-01-02", end: str = "2024-12-31", seed: int = 1) -> pd.DataFrame:
    """A synthetic daily insider series indexed by filing date."""
    days = pd.bdate_range(start, end)
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"buy_filings": rng.poisson(20, len(days)).astype(float),
                         "sell_filings": rng.poisson(60, len(days)).astype(float)}, index=days)


def test_a_filing_counts_only_from_the_next_day():
    """Changing filings dated on or after day d leaves every reading up to d unchanged."""
    base = daily_series()
    z0 = insiders.sentiment(60, 730, base)
    cut = pd.Timestamp("2015-06-10")
    moved = base.copy()
    moved.loc[moved.index >= cut, "buy_filings"] *= 5
    z1 = insiders.sentiment(60, 730, moved)
    upto = z0.index <= cut
    pd.testing.assert_series_equal(z0[upto], z1[upto])
    assert not np.allclose(z0[z0.index > cut].dropna().iloc[:5], z1[z1.index > cut].dropna().iloc[:5])


def test_the_rule_holds_while_insiders_buy_and_never_sees_the_future(monkeypatch):
    """Positions follow the standardised reading at each bar date, and only earlier filings move them."""
    days = pd.bdate_range("2005-01-03", "2024-12-31", tz="UTC")
    bars = normalize_bars(pd.DataFrame({"ts": days, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                                        "volume": 1.0}))
    base = daily_series()
    monkeypatch.setattr(insiders, "series", lambda: base)
    pos = insiders.insider_timing(bars, 60, 730, 0.0)
    assert set(pos.unique()) <= {0.0, 1.0} and pos[bars["ts"] < "2006-06-01"].eq(0.0).all()
    assert 0.2 < pos[bars["ts"] > "2010-01-01"].mean() < 0.8
    cut = pd.Timestamp("2016-03-01")
    moved = base.copy()
    moved.loc[moved.index >= cut, "sell_filings"] = 0.0
    monkeypatch.setattr(insiders, "series", lambda: moved)
    pos2 = insiders.insider_timing(bars, 60, 730, 0.0)
    upto = (bars["ts"] <= pd.Timestamp(cut, tz="UTC")).to_numpy()
    soon = (~upto) & (bars["ts"] < pd.Timestamp("2016-06-01", tz="UTC")).to_numpy()
    assert (pos[upto] == pos2[upto]).all() and (pos2[soon] == 1.0).mean() > 0.95  # a buying spree: hold at once


def test_the_gauntlet_judges_the_rule_only_from_its_start(monkeypatch):
    """Years before the rule's inputs exist are left out of the research period, the cash rate with them."""
    days = pd.bdate_range("2000-01-03", "2024-12-31", tz="UTC")
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(days))))
    bars = normalize_bars(pd.DataFrame({"ts": days, "open": close, "high": close * 1.01, "low": close * 0.99,
                                        "close": close, "volume": 1.0}))
    store.write_series("yahoo", "SPY", "1d", bars)
    monkeypatch.setattr(insiders, "series", lambda: daily_series())
    subject = BarSubject.load("yahoo", "SPY", "1d", "insider_timing", lockbox.sealed_from())
    assert subject.research[0] >= REGISTRY["insider_timing"].starts and subject.question == "insider_timing:SPY:1d"
    sim = subject.simulate(REGISTRY["insider_timing"].defaults())
    assert len(sim.net) == len(subject.days) and np.isfinite(sim.net).all()
