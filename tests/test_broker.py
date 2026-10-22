"""Tests for M7b: the paper-only guard, the Alpaca and Binance testnet adapters (mocked HTTP), the asymmetric controls,
order planning, and the full loop on a fake broker: enroll, send, fill, reconcile, slippage, flatten and kill."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import httpx
import pandas as pd
import pytest
from test_paper import FRAMES, N_DAYS, night, save_card, store_through

from qlab import db
from qlab.broker import controls, execution
from qlab.broker.alpaca import AlpacaPaper
from qlab.broker.base import Account, BrokerError, OrderRequest, OrderState, PaperClient
from qlab.broker.binance_testnet import BinanceTestnet
from qlab.paper import book
from qlab.portfolio import panel as panel_mod

T0 = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)


# --- The paper-only guard and the adapters ----------------------------------------------------------------------


@pytest.mark.parametrize("url", ["https://api.alpaca.markets", "https://api.binance.com", "http://paper-api.alpaca.markets"])
def test_only_paper_hosts_can_be_reached(url):
    """Live endpoints and plain http are refused before any request is made."""
    with pytest.raises(BrokerError, match="only paper and testnet hosts"):
        PaperClient(url)


def _alpaca(monkeypatch, handler, key="PKTESTKEY", secret="sekrit-value"):
    """An Alpaca adapter whose HTTP calls go to ``handler``."""
    monkeypatch.setenv("QLAB_ALPACA_KEY", key)
    monkeypatch.setenv("QLAB_ALPACA_SECRET", secret)
    return AlpacaPaper(httpx.Client(transport=httpx.MockTransport(handler)))


def test_alpaca_sends_keys_as_headers_and_reads_the_account(monkeypatch):
    """Paper keys go in the APCA headers to the paper host; numbers come back as floats."""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["host"], seen["key"] = req.url.host, req.headers["APCA-API-KEY-ID"]
        if req.url.path == "/v2/account":
            return httpx.Response(200, json={"equity": "100000", "cash": "99000", "currency": "USD"})
        return httpx.Response(200, json=[{"symbol": "SPY", "qty": "3"}])

    b = _alpaca(monkeypatch, handler)
    assert b.account() == Account(100000.0, 99000.0, "USD", True)
    assert b.holdings() == {"SPY": 3.0}
    assert seen == {"host": "paper-api.alpaca.markets", "key": "PKTESTKEY"}


def test_alpaca_refuses_live_keys_and_live_endpoints_and_hides_secrets(monkeypatch):
    """A live key (AK…) or a non-paper ALPACA_ENDPOINT stops every call; errors never carry a secret."""
    with pytest.raises(BrokerError, match="live key"):
        _alpaca(monkeypatch, lambda r: None, key="AKLIVE").account()
    monkeypatch.setenv("ALPACA_ENDPOINT", "https://api.alpaca.markets/v2")
    with pytest.raises(BrokerError, match="not the paper API"):
        _alpaca(monkeypatch, lambda r: None).account()
    monkeypatch.delenv("ALPACA_ENDPOINT")

    def refuse(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden for sekrit-value"})

    with pytest.raises(BrokerError) as err:
        _alpaca(monkeypatch, refuse).account()
    assert "sekrit-value" not in str(err.value) and "***" in str(err.value)


def test_alpaca_orders_go_as_market_orders_and_refusals_come_back_rejected(monkeypatch):
    """A submitted order carries the lab's id and time in force; a 422 is a rejected order, not a crash."""
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        if body["symbol"] == "NOPE":
            return httpx.Response(422, json={"message": "asset NOPE not found"})
        return httpx.Response(200, json={"id": "o1", "client_order_id": body["client_order_id"], "status": "accepted",
                                         "filled_qty": "0", "filled_avg_price": None})

    b = _alpaca(monkeypatch, handler)
    ok = b.submit(OrderRequest("q-1", "SPY", "buy", 4, "opg"))
    bad = b.submit(OrderRequest("q-2", "NOPE", "buy", 1, "day"))
    assert ok.status == "accepted" and bad.status == "rejected" and "not found" in bad.message
    assert bodies[0] == {"symbol": "SPY", "qty": "4", "side": "buy", "type": "market", "time_in_force": "opg",
                         "client_order_id": "q-1"}
    assert b.round_qty("SPY", 4.99) == 4.0


def test_binance_signs_requests_and_rounds_to_the_lot_step(monkeypatch):
    """The signature is the HMAC-SHA256 of the query; quantities round down to the step; fills keep their fees."""
    monkeypatch.setenv("QLAB_BINANCE_TESTNET_KEY", "tkey")
    monkeypatch.setenv("QLAB_BINANCE_TESTNET_SECRET", "tsecret")
    checked = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/exchangeInfo":
            return httpx.Response(200, json={"symbols": [{"filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001"},
                {"filterType": "NOTIONAL", "minNotional": "5"}]}]})
        query = urlsplit(str(req.url)).query
        payload, sig = query.rsplit("&signature=", 1)
        checked.append(sig == hmac.new(b"tsecret", payload.encode(), hashlib.sha256).hexdigest()
                       and req.headers["X-MBX-APIKEY"] == "tkey")
        params = dict(parse_qsl(payload))
        if req.url.path == "/api/v3/account":
            return httpx.Response(200, json={"canTrade": True, "balances": [
                {"asset": "USDT", "free": "1000", "locked": "0"}, {"asset": "BTC", "free": "0.5", "locked": "0.1"},
                {"asset": "ETH", "free": "0", "locked": "0"}]})
        return httpx.Response(200, json={
            "symbol": params["symbol"], "orderId": 7, "clientOrderId": params["newClientOrderId"], "status": "FILLED",
            "executedQty": params["quantity"], "cummulativeQuoteQty": "30.5",
            "fills": [{"price": "61000", "qty": params["quantity"], "commission": "0.0000005",
                       "commissionAsset": "BTC", "tradeId": 99}]})

    b = BinanceTestnet(httpx.Client(transport=httpx.MockTransport(handler)), clock=lambda: 1_760_000_000.0)
    assert b.round_qty("BTCUSDT", 0.000509) == pytest.approx(0.0005) and b.min_notional("BTCUSDT") == 5.0
    assert b.holdings() == {"BTCUSDT": pytest.approx(0.6)}
    st = b.submit(OrderRequest("q-3", "BTCUSDT", "buy", 0.0005))
    assert st.status == "filled" and st.fills[0].fee_asset == "BTC" and st.fills[0].fill_id == "BTCUSDT:99"
    assert checked and all(checked)


# --- Controls ----------------------------------------------------------------------------------------------------


def test_reducing_risk_is_instant_and_adding_it_waits_24_hours():
    """Kill now; resume needs the word, a reason and a day; a later kill overrides a pending resume."""
    controls.record_kill("test", now=T0)
    assert controls.state(T0)["mode"] == "killed" and not controls.allowed("e1", T0)[0]
    with pytest.raises(ValueError, match="RESUME"):
        controls.resume("all", "done testing", "yes", now=T0)
    controls.resume("all", "done testing", "RESUME", now=T0 + timedelta(minutes=1))
    assert controls.state(T0 + timedelta(hours=23))["mode"] == "killed"
    assert len(controls.state(T0 + timedelta(hours=23))["pending"]) == 1
    assert controls.state(T0 + timedelta(hours=25))["mode"] == "active"
    controls.record_kill("again", now=T0 + timedelta(hours=2))
    assert controls.state(T0 + timedelta(hours=30))["mode"] == "killed"


def test_tightening_a_limit_is_instant_and_loosening_waits():
    """A lower order cap applies at once; a higher one needs LOOSEN and a day, and a tightening meanwhile wins."""
    controls.set_limits({"max_order_notional": 5000}, "smaller orders", now=T0)
    assert controls.limits(T0)["max_order_notional"] == 5000
    with pytest.raises(ValueError, match="LOOSEN"):
        controls.set_limits({"max_order_notional": 20000}, "bigger", now=T0)
    out = controls.set_limits({"max_order_notional": 20000}, "bigger", confirm="LOOSEN", now=T0)
    assert out["loosened"] == ["max_order_notional"]
    assert controls.limits(T0 + timedelta(hours=1))["max_order_notional"] == 5000
    assert controls.limits(T0 + timedelta(hours=25))["max_order_notional"] == 20000
    controls.set_limits({"max_order_notional": 3000}, "tighter again", now=T0 + timedelta(hours=2))
    assert controls.limits(T0 + timedelta(hours=30))["max_order_notional"] == 3000


# --- Planning ----------------------------------------------------------------------------------------------------


class FakeBroker:
    """An in-memory paper broker: market orders stay open until ``fill_all`` fills them at a price function."""

    def __init__(self, name="alpaca", quote="USD", equity=100_000.0, lot=1.0):
        self.name, self.quote, self.asset_class = name, quote, "equity"
        self.equity, self.lot = equity, lot
        self.orders: dict[str, dict] = {}
        self.held: dict[str, float] = {}
        self.cancelled: list[str] = []

    def configured(self):
        return True

    def account(self):
        return Account(self.equity, self.equity, self.quote)

    def holdings(self):
        return {s: q for s, q in self.held.items() if q}

    def submit(self, o: OrderRequest):
        if o.client_id in self.orders:
            return OrderState(o.client_id, "", "rejected", message="duplicate client id")
        self.orders[o.client_id] = {"req": o, "status": "accepted", "qty": 0.0, "avg": None}
        return OrderState(o.client_id, f"b-{o.client_id}", "accepted")

    def fill_all(self, price):
        for _cid, o in self.orders.items():
            if o["status"] == "accepted":
                o.update(status="filled", qty=o["req"].qty, avg=price(o["req"].symbol))
                sign = 1 if o["req"].side == "buy" else -1
                self.held[o["req"].symbol] = self.held.get(o["req"].symbol, 0.0) + sign * o["req"].qty

    def order(self, client_id, symbol):
        o = self.orders[client_id]
        return OrderState(client_id, f"b-{client_id}", o["status"], o["qty"], o["avg"])

    def cancel(self, client_id, symbol):
        self.orders[client_id]["status"] = "canceled"
        self.cancelled.append(client_id)

    def round_qty(self, symbol, qty):
        return float(int(qty / self.lot + 1e-9)) * self.lot

    def min_notional(self, symbol):
        return 1.0


def test_planning_sizes_whole_units_sells_first_and_stays_long_only():
    """Weights become whole shares of the sleeve's equity at raw prices; shorts and leverage are clipped."""
    b = FakeBroker()
    lim = controls.DEFAULT_LIMITS
    orders, notes, equity = execution.plan_orders({"A": 0.6, "B": 0.6, "C": -0.5}, {"C": 10.0}, 9000.0,
                                                  {"A": 100.0, "B": 50.0, "C": 100.0}, lim, b)
    assert equity == 10_000.0
    assert [o["side"] for o in orders] == ["sell", "buy", "buy"]
    assert {o["symbol"]: o["qty"] for o in orders} == {"C": 10.0, "A": 50.0, "B": 100.0}
    assert any("negative targets" in n for n in notes) and any("scaled" in n for n in notes)


def test_the_auction_is_used_only_inside_its_window():
    """Alpaca orders join the opening auction from 19:00 to 09:28 New York time; Binance orders go at once."""
    nxt = datetime(2026, 10, 20, 13, 30, tzinfo=UTC)  # 09:30 New York
    assert execution.time_in_force("alpaca", datetime(2026, 10, 19, 23, 30, tzinfo=UTC), nxt) == "opg"
    assert execution.time_in_force("alpaca", datetime(2026, 10, 19, 21, 30, tzinfo=UTC), nxt) == "day"
    assert execution.time_in_force("alpaca", datetime(2026, 10, 20, 13, 29, tzinfo=UTC), nxt) == "day"
    assert execution.time_in_force("binance", datetime(2026, 10, 19, 23, 30, tzinfo=UTC), nxt) == "gtc"


# --- The full loop on a fake broker ------------------------------------------------------------------------------


@pytest.fixture
def entry(monkeypatch):
    """A trend-filter entry on two test ETFs, with one decision in the book."""
    monkeypatch.setitem(panel_mod.UNIVERSES, "test_pair",
                        {"members": [("yahoo", "AAA"), ("yahoo", "BBB")], "description": "test"})
    day = store_through(N_DAYS)
    save_card("card_pair", {"kind": "portfolio", "source": "panel", "symbol": "test_pair", "universe": "test_pair",
                            "interval": "1d", "question": "trend_filter:test_pair:1d", "family": "trend_filter",
                            "strategy": "trend_filter", "params": {"window": 60, "rebalance": 7}})
    item = book.add("card_pair", note="test")
    book.run(now=night(day))
    return item, day


def test_enrolling_needs_the_id_typed_back_and_a_reason(entry):
    """Starting a sleeve adds risk: the entry id must be typed and a reason given."""
    item, _ = entry
    with pytest.raises(ValueError, match="type the entry id"):
        execution.enroll(item["entry_id"], "start", "yes")
    sl = execution.enroll(item["entry_id"], "start paper trading", item["entry_id"])
    assert sl["broker"] == "alpaca" and sl["capital"] == 10_000.0


def test_a_decision_becomes_orders_fills_a_clean_reconciliation_and_slippage(entry):
    """Send the latest decision once, fill it, check the account matches, and measure slippage at the next open."""
    item, day = entry
    execution.enroll(item["entry_id"], "start", item["entry_id"])
    b = FakeBroker()
    now = night(day)
    res = execution.execute(now, {"alpaca": b})[0]
    assert res["status"] == "sent" and res["tif"] == "opg" and res["orders"]
    assert execution.execute(now, {"alpaca": b})[0]["status"] == "exists"  # idempotent
    # The open the engine would fill at becomes the fill price, two basis points worse.
    store_through(N_DAYS + 1)
    nxt = FRAMES["AAA"]["ts"].iloc[N_DAYS]
    opens = {s: f.set_index("ts").loc[nxt, "open"] for s, f in FRAMES.items()}
    b.fill_all(lambda s: opens[s] * 1.0002)
    out = execution.sync(now + timedelta(hours=12), {"alpaca": b})
    assert out["fills"] == len(res["orders"]) and out["reconciliation"]["alpaca"]["ok"]
    held, cash = execution.sleeve_book(execution.sleeves()[0], "USD")
    assert held == {o["symbol"]: o["qty"] for o in res["orders"]} and cash < 10_000
    slip = execution.slippage()
    assert slip and all(r["slippage_bps"] == pytest.approx(2.0, abs=1e-6) for r in slip)
    # A manual trade in the account shows up as a mismatch.
    b.held["ZZZ"] = 5
    assert not execution.reconcile(b, now + timedelta(hours=13))["ok"]


def test_catch_up_sends_what_the_auction_left_once(entry):
    """Part-filled and unfilled auction orders get one day order each for the rest, after the open only."""
    item, day = entry
    execution.enroll(item["entry_id"], "start", item["entry_id"])
    b = FakeBroker()
    now = night(day)
    res = execution.execute(now, {"alpaca": b})[0]
    assert len(res["orders"]) == 2
    first, second = res["orders"]
    # The auction fills part of the first order and none of the second; both remainders expire.
    b.orders[first["client_id"]].update(status="expired", qty=first["qty"] - 3, avg=100.0)
    b.orders[second["client_id"]].update(status="expired", qty=0.0, avg=None)
    nxt = book.decisions(item["entry_id"])[-1]["next_open"]
    opened = execution._when(nxt)
    early = execution.catch_up(opened - timedelta(minutes=5), {"alpaca": b})[0]
    assert early["status"] == "waiting" and not any(c.startswith("c-") for c in b.orders)
    out = execution.catch_up(opened + timedelta(minutes=15), {"alpaca": b})[0]
    assert out["status"] == "sent" and {o["symbol"]: o["qty"] for o in out["orders"]} == {
        first["symbol"]: 3.0, second["symbol"]: second["qty"]}
    assert all(o["tif"] == "day" and o["client_id"].startswith("c-") for o in out["orders"])
    again = execution.catch_up(opened + timedelta(minutes=20), {"alpaca": b})[0]
    assert again["status"] == "waiting"  # its own orders are open; nothing is sent twice
    b.fill_all(lambda s: 100.0)
    done = execution.catch_up(opened + timedelta(minutes=30), {"alpaca": b})[0]
    assert done["status"] == "nothing" and sum(c.startswith("c-") for c in b.orders) == 2
    late = execution.catch_up(opened + timedelta(hours=6, minutes=20), {"alpaca": b})[0]
    assert late["status"] in ("nothing", "too_late")


def test_limits_block_orders_and_say_why(entry):
    """An order above the size limit is stored as blocked with the reason, and nothing reaches the broker."""
    item, day = entry
    execution.enroll(item["entry_id"], "start", item["entry_id"])
    controls.set_limits({"max_order_notional": 100}, "tiny orders only", now=night(day) - timedelta(hours=1))
    b = FakeBroker()
    res = execution.execute(night(day), {"alpaca": b})[0]
    assert res["status"] == "blocked" and not b.orders
    assert all(o["status"] == "blocked" and "above" in o["message"] for o in res["orders"])


def test_kill_stops_new_orders_cancels_open_ones_and_flattens(entry):
    """The kill switch holds even before the broker answers, cancels what is open and sells what is held."""
    item, day = entry
    execution.enroll(item["entry_id"], "start", item["entry_id"])
    b = FakeBroker()
    now = night(day)
    res = execution.execute(now, {"alpaca": b})[0]
    b.fill_all(lambda s: 100.0)
    execution.sync(now + timedelta(hours=1), {"alpaca": b})
    out = execution.kill("test the switch", now=now + timedelta(hours=2), brokers={"alpaca": b})
    assert out["state"]["mode"] == "killed"
    sells = out["flatten"][item["entry_id"]]
    assert {o["symbol"] for o in sells} == {o["symbol"] for o in res["orders"]}
    assert all(o["side"] == "sell" and o["client_id"].startswith("k-") for o in sells)
    later = execution.execute(now + timedelta(hours=3), {"alpaca": b})[0]
    assert later["status"] == "blocked" and "killed" in later["notes"][0]
    assert db.rows("SELECT count(*) AS n FROM audit WHERE action = 'broker.kill'")[0]["n"] == 1


def test_flatten_pauses_one_sleeve(entry):
    """Flattening sells one sleeve's holdings and pauses it, leaving the rest of the lab trading."""
    item, day = entry
    execution.enroll(item["entry_id"], "start", item["entry_id"])
    b = FakeBroker()
    now = night(day)
    execution.execute(now, {"alpaca": b})
    b.fill_all(lambda s: 100.0)
    execution.sync(now + timedelta(hours=1), {"alpaca": b})
    out = execution.flatten(item["entry_id"], "done", now=now + timedelta(hours=2), brokers={"alpaca": b})
    assert out["orders"] and controls.state(now + timedelta(hours=2))["entries"][item["entry_id"]] == "paused"
    assert controls.state(now + timedelta(hours=2))["mode"] == "active"


def test_unconfigured_brokers_are_reported_not_called(entry):
    """Without keys a sleeve says so and nothing is sent."""
    item, day = entry
    execution.enroll(item["entry_id"], "start", item["entry_id"])

    class NoKeys(FakeBroker):
        def configured(self):
            return False

    assert execution.execute(night(day), {"alpaca": NoKeys()})[0]["status"] == "not_configured"


def test_crypto_and_static_mixes_cannot_be_enrolled(entry, monkeypatch):
    """A universe with ETFs and crypto would need two brokers, so it can't get a sleeve."""
    item, _ = entry
    monkeypatch.setattr(execution, "_instruments", lambda it: (_ for _ in ()).throw(
        ValueError("this entry mixes ETFs and crypto; a sleeve trades on one broker")))
    with pytest.raises(ValueError, match="mixes"):
        execution.enroll(item["entry_id"], "start", item["entry_id"])


def test_raw_prices_not_adjusted_ones_size_the_orders(entry):
    """Orders are sized from the stored raw close, the price the broker trades at."""
    _, day = entry
    bar = execution.raw_bar("yahoo", "AAA", pd.Timestamp(day))
    assert bar == pytest.approx((FRAMES["AAA"]["open"].iloc[N_DAYS - 1], FRAMES["AAA"]["close"].iloc[N_DAYS - 1]))


def test_tests_never_see_the_developers_broker_keys():
    """The repository's .env is out of reach in tests, so no test can call a real paper account."""
    from qlab.broker.alpaca import AlpacaPaper

    assert not AlpacaPaper().configured()


def test_the_broker_api_reports_status_and_kills_without_a_form():
    """Status sends nothing; the kill needs no confirmation; resuming does."""
    from fastapi.testclient import TestClient

    from qlab.api.main import create_app

    client = TestClient(create_app())
    st = client.get("/api/broker/status").json()
    assert st["controls"]["mode"] == "active" and st["brokers"]["alpaca"]["configured"] is False
    assert client.post("/api/broker/kill", json={}).json()["state"]["mode"] == "killed"
    assert client.post("/api/broker/resume", json={"reason": "ok", "confirm": "yes"}).status_code == 400
    res = client.post("/api/broker/resume", json={"reason": "tested the switch", "confirm": "RESUME"})
    assert res.status_code == 200 and client.get("/api/broker/status").json()["controls"]["pending"]
