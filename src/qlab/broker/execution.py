"""Turn the paper book's decisions into broker paper orders, record what fills, and check the books agree.

Design (decision 0009):

- **Sleeves.** An entry of the paper book trades on a broker only once it is enrolled, with its own slice of the
  paper account (``sleeve_capital``). The sleeve's holdings and cash are computed from the lab's own fills, so
  several entries can share one account and each keeps its own record. US ETFs go to Alpaca, crypto to the Binance
  testnet; a universe that mixes both can't be enrolled.
- **Raw prices for orders.** Research runs on dividend-adjusted prices; orders are sized from the raw close of the
  decision's date, the price the broker actually trades at (Vidovich & Zerjal, 2026: adjusted prices that live orders
  never see were one of six backtest-to-live defects).
- **The researched fill.** Engines fill at the next open. Alpaca orders sent between 19:00 and 09:28 New York time go
  in as market-on-open (``opg``), the opening auction itself; at other times as day orders. Binance trades all day,
  so its market orders go at once. Every order records whether it was sent after the open it was meant for.
- **Limits outside the strategy, before every order** (``controls``): the kill switch and pauses, the largest order,
  orders per day, no leverage inside a sleeve, and all sleeves together below a share of the account. A blocked
  order is stored with the reason and raises an alert. Reducing risk (selling what a sleeve holds, on a kill or a
  flatten) bypasses every limit except "only what the sleeve holds".
- **Catching up what the auction left.** Alpaca's paper engine fills part of an order about one time in ten on
  purpose, and an ``opg`` order's remainder expires once the auction is over: on 2026-10-21 ten of fifteen opening
  orders ended unfilled or part-filled, leaving the sleeve 13% invested against a 71% target. ``catch_up``, run
  after the open, sends each remainder once as a day order (purpose ``catchup``, flagged late), within the same
  limits. Its fills count in slippage against the open, so the cost of missing the auction is measured, not hidden.
- **Idempotent.** One rebalance per entry and decision: the client order id is built from the entry, the decision
  date and the symbol, and brokers refuse a duplicate id. Fills are append-only, keyed by the broker's id.
- **Reconciliation.** Before its first order on a broker, the lab stores the account's holdings as a baseline. After
  every sync, each holding must equal its baseline plus the lab's fills. A difference (a manual trade, a testnet
  reset, a missed fill) is stored and alerted.
- **Slippage against the model.** Each filled rebalance is compared with the raw open the engine would have filled
  at, in basis points against the trade (positive is worse), next to the half-spread plus slippage the cost model
  charges.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .. import alerts, audit, db
from ..backtest.engine import COST_PRESETS
from ..config import EXCHANGE_TZ
from ..data import store
from ..data.ingest import SOURCES
from ..gauntlet.subjects import PanelSubject
from ..paper import book, sessions
from ..timeutil import naive_utc, now_utc
from . import controls
from .base import Broker, BrokerError, OrderRequest, OrderState

NY = ZoneInfo(EXCHANGE_TZ)
BROKER_OF = {"equity": "alpaca", "crypto": "binance"}
OPEN_STATUSES = ("submitting", "new", "accepted", "partially_filled")


def make_brokers() -> dict[str, Broker]:
    """The paper brokers the lab knows, configured from the environment and ``.env`` files.

    Returns:
        ``{"alpaca": AlpacaPaper, "binance": BinanceTestnet}``; check ``configured()`` before use.
    """
    from .alpaca import AlpacaPaper
    from .binance_testnet import BinanceTestnet

    return {"alpaca": AlpacaPaper(), "binance": BinanceTestnet()}


def _alert(rule: str, severity: str, key: str, title: str, message: str) -> None:
    """Send and log one broker alert; never raises.

    Args:
        rule: ``broker_kill``, ``broker_blocked``, ``broker_rejected``, ``broker_mismatch`` or ``broker_error``.
        severity: ``info``, ``warn`` or ``bad``.
        key: Identity of the event.
        title: One line.
        message: Details.
    """
    try:
        item = alerts.Alert(rule=rule, severity=severity, key=key, title=title, message=message, link="/desk")
        alerts.record([item], alerts.deliver([item]))
    except Exception:  # an alert failure must never stop an order path, least of all a kill
        pass


def _when(x: Any) -> datetime:
    """A stored naive-UTC time (datetime or ISO text) as an aware UTC datetime.

    Args:
        x: Value from the database.

    Returns:
        Aware UTC datetime.
    """
    t = x if isinstance(x, datetime) else datetime.fromisoformat(str(x))
    return t.replace(tzinfo=ZoneInfo("UTC")) if t.tzinfo is None else t


# --- Sleeves ---------------------------------------------------------------------------------------------------


def _instruments(item: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """An entry's asset class and the data source of each instrument it can hold.

    Args:
        item: Paper-book entry (decoded).

    Returns:
        ``(asset_class, {symbol: source})``.

    Raises:
        ValueError: The entry mixes equities and crypto, which would need two brokers.
    """
    subject = book.subject_of(item)
    if isinstance(subject, PanelSubject):
        classes = set(subject.panel.asset_class)
        sources = dict(zip(subject.panel.symbols, subject.panel.sources, strict=True))
    else:
        info = SOURCES.get(subject.meta["source"])
        classes = {info.asset_class if info else "equity"}
        sources = {subject.meta["symbol"]: subject.meta["source"]}
    if len(classes) != 1:
        raise ValueError("this entry mixes ETFs and crypto; a sleeve trades on one broker")
    return classes.pop(), sources


def sleeves(status: str | None = None) -> list[dict[str, Any]]:
    """Enrolled entries.

    Args:
        status: ``active`` or ``closed``; None returns all.

    Returns:
        Sleeve rows, oldest first.
    """
    where, args = ("WHERE status = ?", [status]) if status else ("", [])
    return db.rows(f"SELECT * FROM broker_sleeves {where} ORDER BY enrolled_at", args)


def enroll(entry_id: str, reason: str, confirm: str, actor: str = "user", now: datetime | None = None) -> dict:
    """Give a paper-book entry a sleeve on its broker, so its decisions become paper orders.

    Starting a strategy adds risk, so it needs the entry id typed back and a reason. The sleeve gets the current
    ``sleeve_capital``.

    Args:
        entry_id: Paper-book entry.
        reason: Why (required).
        confirm: Must equal ``entry_id``.
        actor: Who.
        now: Time (tz-aware); None means now.

    Returns:
        The sleeve row.

    Raises:
        KeyError: Unknown entry.
        ValueError: Wrong confirmation, no reason, a retired entry, a mixed universe or an existing sleeve.
    """
    now = now or now_utc()
    if confirm.strip() != entry_id:
        raise ValueError(f"type the entry id ({entry_id}) to confirm")
    if not reason.strip():
        raise ValueError("a reason is required")
    item = book.entry(entry_id)
    if item["status"] != "active":
        raise ValueError("the entry is retired")
    if db.rows("SELECT 1 FROM broker_sleeves WHERE entry_id = ?", [entry_id]):
        raise ValueError("this entry already has a sleeve")
    asset_class, _ = _instruments(item)
    capital = controls.limits(now)["sleeve_capital"]
    with db.connect() as con:
        con.execute("INSERT INTO broker_sleeves VALUES (?, ?, ?, 'active', ?, ?, ?)",
                    [entry_id, BROKER_OF[asset_class], capital, naive_utc(now), reason, actor])
    audit.log("broker.enroll", target=entry_id, actor=actor, reason=reason,
              details={"broker": BROKER_OF[asset_class], "capital": capital, "question": item["question"]})
    return db.rows("SELECT * FROM broker_sleeves WHERE entry_id = ?", [entry_id])[0]


def _fills(entry_id: str | None = None, broker: str | None = None) -> list[dict[str, Any]]:
    """Fills of the lab's orders, with each order's entry and purpose.

    Args:
        entry_id: Only this sleeve's fills.
        broker: Only this broker's fills.

    Returns:
        Fill rows plus ``entry_id`` and ``purpose``, oldest first.
    """
    clauses, args = [], []
    if entry_id:
        clauses.append("o.entry_id = ?")
        args.append(entry_id)
    if broker:
        clauses.append("f.broker = ?")
        args.append(broker)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return db.rows(f"""SELECT f.*, o.entry_id, o.purpose FROM broker_fills f
                       JOIN broker_orders o ON o.client_id = f.client_id {where}
                       ORDER BY f.filled_at, f.fill_id""", args)


def _signed(fill: dict[str, Any], quote: str) -> tuple[float, float]:
    """A fill's change to holdings and to cash.

    Args:
        fill: Fill row.
        quote: The broker's cash currency (``USD`` or ``USDT``).

    Returns:
        ``(units, cash)``: units bought (negative when sold, less any fee charged in the coin) and cash received
        (negative when spent, less any fee charged in cash).
    """
    sign = 1.0 if fill["side"] == "buy" else -1.0
    units = sign * fill["qty"]
    cash = -sign * fill["qty"] * fill["price"]
    fee, asset = fill["fee"] or 0.0, fill["fee_asset"] or ""
    if asset and asset == quote:
        cash -= fee
    elif asset and fill["symbol"].startswith(asset) and fill["symbol"] != asset:
        units -= fee  # Binance charges a buy's fee in the coin bought
    elif not asset:
        cash -= fee
    return units, cash


def sleeve_book(sleeve: dict[str, Any], quote: str) -> tuple[dict[str, float], float]:
    """A sleeve's holdings and cash from its fills.

    Args:
        sleeve: Sleeve row.
        quote: Cash currency of its broker.

    Returns:
        ``(holdings, cash)``: units per symbol (dust below 1e-12 dropped) and cash.
    """
    held: dict[str, float] = {}
    cash = float(sleeve["capital"])
    for f in _fills(sleeve["entry_id"]):
        units, money = _signed(f, quote)
        held[f["symbol"]] = held.get(f["symbol"], 0.0) + units
        cash += money
    return {s: q for s, q in held.items() if abs(q) > 1e-12}, cash


# --- Prices ----------------------------------------------------------------------------------------------------


def raw_bar(source: str, symbol: str, day: pd.Timestamp) -> tuple[float, float] | None:
    """Raw (unadjusted) open and close of one stored daily bar.

    Args:
        source: Data source.
        symbol: Instrument.
        day: Session date (UTC midnight).

    Returns:
        ``(open, close)``, or None when the bar isn't stored.
    """
    try:
        bars = store.read_series(source, symbol, "1d")
    except FileNotFoundError:
        return None
    day = pd.Timestamp(day).tz_convert("UTC").normalize() if pd.Timestamp(day).tzinfo else pd.Timestamp(day, tz="UTC")
    row = bars[pd.DatetimeIndex(bars["ts"]).tz_convert("UTC").normalize() == day]
    if row.empty:
        return None
    return float(row["open"].iloc[-1]), float(row["close"].iloc[-1])


# --- Planning --------------------------------------------------------------------------------------------------


def plan_orders(targets: dict[str, float], held: dict[str, float], cash: float, prices: dict[str, float],
                lim: dict[str, float], broker: Broker) -> tuple[list[dict[str, Any]], list[str], float]:
    """Orders that move a sleeve from what it holds to its target weights.

    Weights are clipped to long-only (spot accounts can't short) and scaled down if they add up to more than
    ``max_sleeve_gross``. Target units are the weight times the sleeve's equity over the raw price, rounded down to
    the broker's lot size. Trades worth less than ``min_order_notional`` (or the broker's minimum) are skipped. Sells
    come first; buys are cut back if they would spend more cash than the sleeve has.

    Args:
        targets: Weight per symbol from the paper book's decision.
        held: Units the sleeve holds.
        cash: The sleeve's cash.
        prices: Raw close per symbol at the decision's date.
        lim: Limits in force.
        broker: Its broker (for lot sizes and minimums).

    Returns:
        ``(orders, notes, equity)``: dicts with ``symbol, side, qty, ref_price, notional``; plain-language notes on
        anything clipped or skipped; and the sleeve's equity at those prices.
    """
    notes: list[str] = []
    missing = [s for s in set(held) | {s for s, w in targets.items() if w > 0} if s not in prices]
    if missing:
        notes.append(f"no raw price for {', '.join(sorted(missing))}; left unchanged")
    equity = cash + sum(q * prices[s] for s, q in held.items() if s in prices)
    w = {s: max(0.0, float(x)) for s, x in targets.items() if s in prices}
    if any(float(x) < 0 for x in targets.values()):
        notes.append("negative targets set to zero: the paper accounts are spot accounts and the lab doesn't short")
    gross = sum(w.values())
    if gross > lim["max_sleeve_gross"] + 1e-9:
        notes.append(f"target gross {gross:.2f} scaled to {lim['max_sleeve_gross']:.2f} (no leverage in a sleeve)")
        w = {s: x * lim["max_sleeve_gross"] / gross for s, x in w.items()}
    orders = []
    for s in sorted(set(w) | {s for s in held if s in prices}):
        price = prices[s]
        want = broker.round_qty(s, w.get(s, 0.0) * equity / price) if w.get(s, 0.0) > 0 else 0.0
        have = held.get(s, 0.0)
        delta = want - have
        if delta < 0:
            qty = have if want == 0 else broker.round_qty(s, -delta)
            side = "sell"
        else:
            qty, side = broker.round_qty(s, delta), "buy"
        notional = qty * price
        if qty <= 0 or notional < max(lim["min_order_notional"], broker.min_notional(s)):
            if qty > 0:
                notes.append(f"{s}: {side} of {notional:.2f} skipped as smaller than the minimum order")
            continue
        orders.append({"symbol": s, "side": side, "qty": qty, "ref_price": price, "notional": notional})
    sells = [o for o in orders if o["side"] == "sell"]
    buys = [o for o in orders if o["side"] == "buy"]
    budget = cash + sum(o["notional"] for o in sells)
    spend = sum(o["notional"] for o in buys)
    if spend > budget + 1e-6 and spend > 0:
        k = max(budget, 0.0) / spend
        notes.append(f"buys cut to {k:.0%} to stay within the sleeve's cash")
        cut = []
        for o in buys:
            qty = broker.round_qty(o["symbol"], o["qty"] * k)
            if qty > 0 and qty * o["ref_price"] >= max(lim["min_order_notional"], broker.min_notional(o["symbol"])):
                cut.append({**o, "qty": qty, "notional": qty * o["ref_price"]})
        buys = cut
    return sells + buys, notes, equity


def time_in_force(broker: str, now: datetime, next_open: datetime) -> str:
    """How to send a rebalance so it fills as close to the researched open as the broker allows.

    Args:
        broker: ``alpaca`` or ``binance``.
        now: Time of sending (tz-aware).
        next_open: The open the decision was meant to fill at (tz-aware).

    Returns:
        ``opg`` for Alpaca between 19:00 and 09:28 New York time before the open (the opening auction), ``day``
        otherwise for Alpaca, ``gtc`` for Binance (market orders fill at once).
    """
    if broker != "alpaca":
        return "gtc"
    ny = now.astimezone(NY)
    minutes = ny.hour * 60 + ny.minute
    if now < next_open - timedelta(minutes=2) and (minutes >= 19 * 60 or minutes <= 9 * 60 + 28):
        return "opg"
    return "day"


def client_id(prefix: str, entry_id: str, stamp: str, symbol: str) -> str:
    """The lab's id for an order: unique per purpose, entry, time stamp and symbol, at most 36 characters.

    Args:
        prefix: ``q`` for a rebalance, ``k`` for a kill or flatten.
        entry_id: Paper-book entry.
        stamp: Decision date (``yymmdd``) or the time of a kill (``yymmddHHMM``).
        symbol: Instrument.

    Returns:
        Text such as ``q-922c0528-261019-SPY`` (Binance allows 36 characters).
    """
    return f"{prefix}-{entry_id[:8]}-{stamp}-{symbol}"[:36]


# --- Recording -------------------------------------------------------------------------------------------------


def _record_state(row: dict[str, Any], state: OrderState, now: datetime) -> list[dict[str, Any]]:
    """Store what the broker says about an order and any new fills.

    Brokers that list each execution (Binance) give fills with ids; for Alpaca, which reports only the cumulative
    quantity and average price, each increase since the stored state becomes one fill at the implied price.

    Args:
        row: The order's stored row.
        state: Its state from the broker.
        now: Time of recording (tz-aware).

    Returns:
        The new fill rows.
    """
    new = []
    if state.fills:
        known = {r["fill_id"] for r in db.rows("SELECT fill_id FROM broker_fills WHERE client_id = ?",
                                               [row["client_id"]])}
        for f in state.fills:
            if f.fill_id not in known:
                new.append({"fill_id": f.fill_id, "qty": f.qty, "price": f.price, "fee": f.fee,
                            "fee_asset": f.fee_asset, "filled_at": f.filled_at})
    else:
        q0, a0 = float(row.get("filled_qty") or 0.0), float(row.get("avg_price") or 0.0)
        if state.avg_price is not None and state.filled_qty > q0 + 1e-12:
            price = (state.avg_price * state.filled_qty - a0 * q0) / (state.filled_qty - q0)
            new.append({"fill_id": f"{state.broker_id}:{state.filled_qty:g}", "qty": state.filled_qty - q0,
                        "price": price, "fee": 0.0, "fee_asset": "", "filled_at": state.updated_at})
    with db.connect() as con:
        con.execute("""UPDATE broker_orders SET broker_id = ?, status = ?, filled_qty = ?, avg_price = ?,
                       message = ?, updated_at = ? WHERE client_id = ?""",
                    [state.broker_id or row.get("broker_id"), state.status, state.filled_qty, state.avg_price,
                     state.message[:500], naive_utc(now), row["client_id"]])
        for f in new:
            when = _fill_time(f["filled_at"], now)
            con.execute("INSERT INTO broker_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                        [f["fill_id"], row["client_id"], row["broker"], row["symbol"], row["side"], f["qty"],
                         f["price"], f["fee"], f["fee_asset"], when, naive_utc(now)])
    return new


def _fill_time(raw: Any, now: datetime) -> datetime:
    """A broker's fill time as naive UTC: ISO text, Unix milliseconds, or now when missing.

    Args:
        raw: What the broker sent.
        now: Fallback (tz-aware).

    Returns:
        Naive UTC datetime.
    """
    text = str(raw or "").strip()
    if text.isdigit():
        return naive_utc(datetime.fromtimestamp(int(text) / 1000, tz=ZoneInfo("UTC")))
    if text:
        try:
            return naive_utc(_when(text.replace("Z", "+00:00")))
        except ValueError:
            pass
    return naive_utc(now)


def _send(broker: Broker, row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Store an order as being submitted, send it, and store the answer.

    Args:
        broker: The broker.
        row: Order fields (``client_id, entry_id, purpose, as_of, symbol, side, qty, tif, ref_price, late``).
        now: Time (tz-aware).

    Returns:
        The row with the broker's ``status`` and ``message``.
    """
    full = {**row, "broker": broker.name, "planned_at": naive_utc(now), "submitted_at": naive_utc(now),
            "broker_id": "", "status": "submitting", "filled_qty": 0.0, "avg_price": None, "message": "",
            "updated_at": naive_utc(now)}
    cols = ["client_id", "entry_id", "broker", "purpose", "as_of", "symbol", "side", "qty", "tif", "ref_price",
            "planned_at", "submitted_at", "late", "broker_id", "status", "filled_qty", "avg_price", "message",
            "updated_at"]
    with db.connect() as con:
        con.execute(f"INSERT INTO broker_orders ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [full[c] for c in cols])
    try:
        state = broker.submit(OrderRequest(row["client_id"], row["symbol"], row["side"], row["qty"], row["tif"]))
    except BrokerError as err:
        state = OrderState(client_id=row["client_id"], broker_id="", status="submitting", message=str(err))
    _record_state(full, state, now)
    return {**full, "status": state.status, "message": state.message}


def _block(row: dict[str, Any], broker: str, why: str, now: datetime) -> dict[str, Any]:
    """Store an order that a limit stopped, and alert.

    Args:
        row: Order fields.
        broker: Broker name.
        why: The limit and the numbers.
        now: Time (tz-aware).

    Returns:
        The stored row.
    """
    full = {**row, "broker": broker, "planned_at": naive_utc(now), "submitted_at": None, "broker_id": "",
            "status": "blocked", "filled_qty": 0.0, "avg_price": None, "message": why, "updated_at": naive_utc(now)}
    cols = list(full)
    with db.connect() as con:
        con.execute(f"INSERT INTO broker_orders ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [full[c] for c in cols])
    _alert("broker_blocked", "warn", row["client_id"], f"Order blocked: {row['side']} {row['symbol']}",
           f"{row['entry_id']}: {why}")
    return full


# --- The daily steps -------------------------------------------------------------------------------------------


def _lab_value(broker: Broker, quote: str) -> float:
    """Value of everything every sleeve on a broker holds, at the latest raw closes.

    Args:
        broker: The broker.
        quote: Its cash currency.

    Returns:
        Value in the broker's currency (holdings without a stored price count as zero).
    """
    total = 0.0
    for sl in sleeves("active"):
        if sl["broker"] != broker.name:
            continue
        held, _ = sleeve_book(sl, quote)
        _, sources = _instruments(book.entry(sl["entry_id"]))
        for s, q in held.items():
            last = _last_close(sources.get(s, "binance" if broker.name == "binance" else "yahoo"), s)
            total += q * (last or 0.0)
    return total


def _last_close(source: str, symbol: str) -> float | None:
    """The latest stored raw close of an instrument.

    Args:
        source: Data source.
        symbol: Instrument.

    Returns:
        The close, or None.
    """
    try:
        bars = store.read_series(source, symbol, "1d")
    except FileNotFoundError:
        return None
    return float(bars["close"].iloc[-1]) if len(bars) else None


def _orders_today(now: datetime) -> int:
    """Orders the lab has sent since UTC midnight (blocked ones not counted).

    Args:
        now: Time (tz-aware).

    Returns:
        Count.
    """
    start = naive_utc(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar("SELECT count(*) FROM broker_orders WHERE planned_at >= ? AND status <> 'blocked'",
                         [start]) or 0)


def _limit_breach(o: dict[str, Any], lim: dict[str, float], now: datetime, lab_value: float,
                  account_equity: float, tradable: bool) -> str:
    """The first limit an order would break, in plain words.

    Args:
        o: Planned order (``side``, ``notional``).
        lim: Limits in force.
        now: Time (tz-aware).
        lab_value: What every sleeve on the broker holds before this order.
        account_equity: The account's equity (plus the lab's holdings on Binance, where equity is cash only).
        tradable: Whether the broker allows trading on the account.

    Returns:
        The reason, or an empty string when the order is within every limit.
    """
    if o["notional"] > lim["max_order_notional"]:
        return f"order of {o['notional']:,.0f} is above the {lim['max_order_notional']:,.0f} limit"
    if _orders_today(now) >= lim["max_orders_per_day"]:
        return f"already {lim['max_orders_per_day']:.0f} orders today"
    if o["side"] == "buy" and lab_value + o["notional"] > lim["max_account_share"] * account_equity:
        return (f"all sleeves would hold {lab_value + o['notional']:,.0f}, above "
                f"{lim['max_account_share']:.0%} of the account ({account_equity:,.0f})")
    if not tradable:
        return "the broker has blocked trading on this account"
    return ""


def execute(now: datetime | None = None, brokers: dict[str, Broker] | None = None,
            actor: str = "user") -> list[dict[str, Any]]:
    """Send each enrolled entry's latest decision to its broker, within every limit.

    Args:
        now: Time (tz-aware); None means now.
        brokers: Broker objects by name; None builds them from the settings.
        actor: Who ran it, for the audit log.

    Returns:
        One result per active sleeve: ``entry_id, question, status`` (``sent``, ``exists``, ``blocked``,
        ``not_configured``, ``no_decision``, ``too_late`` or ``error``), ``as_of``, ``orders`` and ``notes``.
    """
    now = now or now_utc()
    brokers = brokers if brokers is not None else make_brokers()
    lim = controls.limits(now)
    results = []
    for sl in sleeves("active"):
        out: dict[str, Any] = {"entry_id": sl["entry_id"], "status": "", "as_of": None, "orders": [], "notes": []}
        try:
            item = book.entry(sl["entry_id"])
            out["question"] = item["question"]
            out.update(_execute_one(sl, item, brokers.get(sl["broker"]), lim, now))
        except (BrokerError, KeyError, ValueError, FileNotFoundError) as err:
            out.update(status="error", notes=[str(err)])
            _alert("broker_error", "warn", f"error:{sl['entry_id']}:{naive_utc(now).date()}",
                   f"Broker step failed for {sl['entry_id']}", str(err))
        results.append(out)
    audit.log("broker.execute", target="sleeves", actor=actor,
              details={s: sum(r["status"] == s for r in results)
                       for s in ("sent", "exists", "blocked", "not_configured", "too_late", "error")})
    return results


def _execute_one(sl: dict[str, Any], item: dict[str, Any], broker: Broker | None, lim: dict[str, float],
                 now: datetime) -> dict[str, Any]:
    """Plan and send one sleeve's rebalance for its latest decision.

    Args:
        sl: Sleeve row.
        item: Its paper-book entry.
        broker: Its broker, or None.
        lim: Limits in force.
        now: Time (tz-aware).

    Returns:
        ``status``, ``as_of``, ``orders`` and ``notes`` (see ``execute``).

    Raises:
        BrokerError: The broker couldn't be reached while planning.
    """
    if item["status"] != "active":
        return {"status": "blocked", "notes": ["the paper-book entry is retired"]}
    ok, why = controls.allowed(sl["entry_id"], now)
    if not ok:
        return {"status": "blocked", "notes": [why]}
    if broker is None or not broker.configured():
        return {"status": "not_configured", "notes": [f"set the {sl['broker']} paper keys to trade this sleeve"]}
    decs = book.decisions(sl["entry_id"])
    if not decs:
        return {"status": "no_decision", "notes": ["the book has no decision for this entry yet"]}
    d = decs[-1]
    as_of = str(d["as_of"])[:10]
    if db.rows("SELECT 1 FROM broker_orders WHERE entry_id = ? AND as_of = ? AND purpose = 'rebalance' LIMIT 1",
               [sl["entry_id"], as_of]):
        return {"status": "exists", "as_of": as_of, "notes": [f"the decision of {as_of} was already sent"]}
    asset_class, sources = _instruments(item)
    next_open = _when(d["next_open"])
    session_close = sessions.first_close(pd.Timestamp(next_open.date(), tz="UTC"), [asset_class])
    if now >= session_close:
        return {"status": "too_late", "as_of": as_of,
                "notes": [f"the session the decision of {as_of} was meant for has closed; waiting for the next one"]}
    sync(now, {broker.name: broker})
    ensure_baseline(broker, now)
    held, cash = sleeve_book(sl, broker.quote)
    day = pd.Timestamp(as_of, tz="UTC")
    prices = {}
    for s in set(d["targets"]) | set(held):
        bar = raw_bar(sources.get(s, "yahoo"), s, day)
        if bar:
            prices[s] = bar[1]
    orders, notes, equity = plan_orders(d["targets"], held, cash, prices, lim, broker)
    tif = time_in_force(broker.name, now, next_open)
    late = now > next_open
    sent, blocked = [], 0
    account = broker.account()
    lab_value = _lab_value(broker, broker.quote)
    account_equity = account.equity + (lab_value if broker.name == "binance" else 0.0)
    for o in orders:
        row = {"client_id": client_id("q", sl["entry_id"], day.strftime("%y%m%d"), o["symbol"]),
               "entry_id": sl["entry_id"], "purpose": "rebalance", "as_of": day.date(), "symbol": o["symbol"],
               "side": o["side"], "qty": o["qty"], "tif": tif, "ref_price": o["ref_price"], "late": late}
        why = _limit_breach(o, lim, now, lab_value, account_equity, account.tradable)
        if why:
            sent.append(_block(row, broker.name, why, now))
            blocked += 1
            continue
        res = _send(broker, row, now)
        if res["status"] == "rejected" and tif == "opg" and "opg" in res["message"].lower():
            res = _send(broker, {**row, "client_id": row["client_id"][:34] + "-d", "tif": "day"}, now)
            notes.append(f"{o['symbol']}: the opening auction refused the order; sent as a day order")
        if res["status"] == "rejected":
            _alert("broker_rejected", "warn", res["client_id"], f"Order rejected: {o['side']} {o['symbol']}",
                   f"{sl['entry_id']}: {res['message']}")
        if o["side"] == "buy" and res["status"] != "rejected":
            lab_value += o["notional"]
        sent.append(res)
    status = "sent" if sent and blocked < len(sent) else ("blocked" if blocked else "sent")
    if not orders:
        notes.append("already at target; nothing to trade")
    return {"status": status, "as_of": as_of, "equity": equity, "late": late, "tif": tif, "notes": notes,
            "orders": [{k: r[k] for k in ("client_id", "symbol", "side", "qty", "tif", "status", "message")}
                       for r in sent]}


def catch_up(now: datetime | None = None, brokers: dict[str, Broker] | None = None,
             actor: str = "user") -> list[dict[str, Any]]:
    """After the open, send once what each sleeve's latest rebalance left unfilled, as day orders.

    Runs ``sync`` first, so expired auction orders are known. For each active sleeve whose latest decision's
    rebalance orders are all final, every order that ended ``expired`` or ``canceled`` with units left gets one
    ``catchup`` order for the rest (never more than the sleeve holds for a sell; buys cut to the sleeve's cash),
    within every limit. Nothing is sent before the open the decision was meant for, or in the last 15 minutes of
    its session.

    Args:
        now: Time (tz-aware); None means now.
        brokers: Broker objects by name; None builds them from the settings.
        actor: Who ran it, for the audit log.

    Returns:
        One result per active sleeve: ``entry_id, status`` (``sent``, ``nothing``, ``waiting``, ``blocked``,
        ``not_configured``, ``too_late`` or ``error``), ``as_of``, ``orders`` and ``notes``.
    """
    now = now or now_utc()
    brokers = brokers if brokers is not None else make_brokers()
    sync(now, brokers)
    lim = controls.limits(now)
    results = []
    for sl in sleeves("active"):
        out: dict[str, Any] = {"entry_id": sl["entry_id"], "status": "", "as_of": None, "orders": [], "notes": []}
        try:
            out.update(_catch_up_one(sl, brokers.get(sl["broker"]), lim, now))
        except (BrokerError, KeyError, ValueError, FileNotFoundError) as err:
            out.update(status="error", notes=[str(err)])
            _alert("broker_error", "warn", f"catchup:{sl['entry_id']}:{naive_utc(now).date()}",
                   f"Catch-up failed for {sl['entry_id']}", str(err))
        results.append(out)
    audit.log("broker.catchup", target="sleeves", actor=actor,
              details={s: sum(r["status"] == s for r in results) for s in ("sent", "nothing", "blocked", "error")})
    return results


def _catch_up_one(sl: dict[str, Any], broker: Broker | None, lim: dict[str, float],
                  now: datetime) -> dict[str, Any]:
    """Send one sleeve's unfilled remainders of its latest rebalance.

    Args:
        sl: Sleeve row.
        broker: Its broker, or None.
        lim: Limits in force.
        now: Time (tz-aware).

    Returns:
        ``status``, ``as_of``, ``orders`` and ``notes`` (see ``catch_up``).
    """
    ok, why = controls.allowed(sl["entry_id"], now)
    if not ok:
        return {"status": "blocked", "notes": [why]}
    if broker is None or not broker.configured():
        return {"status": "not_configured", "notes": [f"set the {sl['broker']} paper keys to trade this sleeve"]}
    decs = book.decisions(sl["entry_id"])
    if not decs:
        return {"status": "nothing", "notes": ["no decision yet"]}
    d = decs[-1]
    as_of = str(d["as_of"])[:10]
    rows = db.rows("SELECT * FROM broker_orders WHERE entry_id = ? AND as_of = ? ORDER BY planned_at",
                   [sl["entry_id"], as_of])
    rebal = [r for r in rows if r["purpose"] == "rebalance"]
    if not rebal:
        return {"status": "nothing", "as_of": as_of, "notes": [f"the decision of {as_of} hasn't been sent"]}
    if any(r["status"] in OPEN_STATUSES for r in rows):
        return {"status": "waiting", "as_of": as_of, "notes": ["orders of this decision are still open"]}
    item = book.entry(sl["entry_id"])
    asset_class, _ = _instruments(item)
    next_open = _when(d["next_open"])
    close = sessions.first_close(pd.Timestamp(next_open.date(), tz="UTC"), [asset_class])
    if now < next_open:
        return {"status": "waiting", "as_of": as_of, "notes": ["the open this decision is for hasn't come yet"]}
    if now >= close - timedelta(minutes=15):
        return {"status": "too_late", "as_of": as_of, "notes": ["its session is about to close or has closed"]}
    done = {r["symbol"] for r in rows if r["purpose"] == "catchup"}
    held, cash = sleeve_book(sl, broker.quote)
    stamp = pd.Timestamp(as_of).strftime("%y%m%d")
    plan = []
    for r in rebal:
        left = broker.round_qty(r["symbol"], float(r["qty"]) - float(r["filled_qty"] or 0.0))
        if r["status"] not in ("expired", "canceled") or left <= 0 or r["symbol"] in done:
            continue
        if r["side"] == "sell":
            left = min(left, held.get(r["symbol"], 0.0))
        notional = left * float(r["ref_price"] or 0.0)
        if left > 0 and notional >= max(lim["min_order_notional"], broker.min_notional(r["symbol"])):
            plan.append({"symbol": r["symbol"], "side": r["side"], "qty": left, "ref_price": r["ref_price"],
                         "notional": notional})
    notes: list[str] = []
    sells = [o for o in plan if o["side"] == "sell"]
    buys = [o for o in plan if o["side"] == "buy"]
    budget = cash + sum(o["notional"] for o in sells)
    spend = sum(o["notional"] for o in buys)
    if buys and spend > budget + 1e-6:
        k = max(budget, 0.0) / spend
        notes.append(f"buys cut to {k:.0%} to stay within the sleeve's cash")
        buys = [{**o, "qty": q, "notional": q * o["ref_price"]} for o in buys
                if (q := broker.round_qty(o["symbol"], o["qty"] * k)) > 0]
    if not sells + buys:
        return {"status": "nothing", "as_of": as_of, "notes": ["every order of this decision filled"]}
    account = broker.account()
    lab_value = _lab_value(broker, broker.quote)
    account_equity = account.equity + (lab_value if broker.name == "binance" else 0.0)
    tif = "day" if broker.name == "alpaca" else "gtc"
    sent, blocked = [], 0
    for o in sells + buys:
        row = {"client_id": client_id("c", sl["entry_id"], stamp, o["symbol"]), "entry_id": sl["entry_id"],
               "purpose": "catchup", "as_of": pd.Timestamp(as_of).date(), "symbol": o["symbol"], "side": o["side"],
               "qty": o["qty"], "tif": tif, "ref_price": o["ref_price"], "late": True}
        why = _limit_breach(o, lim, now, lab_value, account_equity, account.tradable)
        if why:
            sent.append(_block(row, broker.name, why, now))
            blocked += 1
            continue
        res = _send(broker, row, now)
        if res["status"] == "rejected":
            _alert("broker_rejected", "warn", res["client_id"], f"Catch-up rejected: {o['side']} {o['symbol']}",
                   f"{sl['entry_id']}: {res['message']}")
        if o["side"] == "buy" and res["status"] != "rejected":
            lab_value += o["notional"]
        sent.append(res)
    return {"status": "blocked" if blocked == len(sent) else "sent", "as_of": as_of, "notes": notes,
            "orders": [{k: r[k] for k in ("client_id", "symbol", "side", "qty", "tif", "status", "message")}
                       for r in sent]}


def sync(now: datetime | None = None, brokers: dict[str, Broker] | None = None) -> dict[str, Any]:
    """Ask the brokers about every order that isn't final, store new fills, and reconcile each account.

    Args:
        now: Time (tz-aware); None means now.
        brokers: Broker objects by name; None builds them from the settings.

    Returns:
        ``updated`` (orders whose state was fetched), ``fills`` (new fills), ``errors`` and ``reconciliation``
        (per broker, see ``reconcile``).
    """
    now = now or now_utc()
    brokers = brokers if brokers is not None else make_brokers()
    placeholders = ", ".join("?" * len(OPEN_STATUSES))
    rows = db.rows(f"SELECT * FROM broker_orders WHERE status IN ({placeholders}) ORDER BY planned_at",
                   list(OPEN_STATUSES))
    updated, fills, errors = 0, 0, []
    for row in rows:
        b = brokers.get(row["broker"])
        if b is None or not b.configured():
            continue
        try:
            state = b.order(row["client_id"], row["symbol"])
        except BrokerError as err:
            if row["status"] == "submitting":  # the broker never got it; the next decision will plan again
                with db.connect() as con:
                    con.execute("UPDATE broker_orders SET status = 'failed', message = ?, updated_at = ? "
                                "WHERE client_id = ?", [str(err)[:500], naive_utc(now), row["client_id"]])
            errors.append(f"{row['client_id']}: {err}")
            continue
        fills += len(_record_state(row, state, now))
        updated += 1
    recon = {}
    for name, b in brokers.items():
        if b.configured() and db.rows("SELECT 1 FROM broker_orders WHERE broker = ? LIMIT 1", [name]):
            try:
                recon[name] = reconcile(b, now)
            except BrokerError as err:
                errors.append(f"{name}: {err}")
    return {"updated": updated, "fills": fills, "errors": errors, "reconciliation": recon}


def ensure_baseline(broker: Broker, now: datetime) -> None:
    """Store an account's holdings before the lab's first order on it, once.

    Args:
        broker: The broker.
        now: Time (tz-aware).

    Raises:
        BrokerError: The holdings can't be read.
    """
    if db.rows("SELECT 1 FROM broker_baselines WHERE broker = ? LIMIT 1", [broker.name]):
        return
    held = broker.holdings()
    with db.connect() as con:
        con.execute("INSERT INTO broker_baselines VALUES (?, '__taken__', 0, ?)", [broker.name, naive_utc(now)])
        for s, q in held.items():
            con.execute("INSERT INTO broker_baselines VALUES (?, ?, ?, ?)", [broker.name, s, q, naive_utc(now)])
    audit.log("broker.baseline", target=broker.name, details={"holdings": len(held)})


def reconcile(broker: Broker, now: datetime | None = None) -> dict[str, Any]:
    """Check every holding the lab has touched equals its baseline plus the lab's fills.

    Args:
        broker: The broker.
        now: Time (tz-aware); None means now.

    Returns:
        ``ok`` and ``mismatches`` (symbol to ``expected`` and ``actual`` units). Stored as a row; a new mismatch alerts.

    Raises:
        BrokerError: The holdings can't be read.
    """
    now = now or now_utc()
    ensure_baseline(broker, now)
    base = {r["symbol"]: r["qty"] for r in db.rows("SELECT symbol, qty FROM broker_baselines WHERE broker = ? "
                                                    "AND symbol <> '__taken__'", [broker.name])}
    expected = dict(base)
    for f in _fills(broker=broker.name):
        units, _ = _signed(f, broker.quote)
        expected[f["symbol"]] = expected.get(f["symbol"], 0.0) + units
    actual = broker.holdings()
    mismatches = {}
    for s in set(expected) | set(actual):
        e, a = expected.get(s, 0.0), actual.get(s, 0.0)
        if abs(e - a) > 1e-6 * max(1.0, abs(e)):
            mismatches[s] = {"expected": e, "actual": a}
    ok = not mismatches
    prev = db.rows("SELECT ok FROM broker_reconciliations WHERE broker = ? ORDER BY ts DESC LIMIT 1", [broker.name])
    with db.connect() as con:
        con.execute("INSERT INTO broker_reconciliations VALUES (?, ?, ?, ?)",
                    [naive_utc(now), broker.name, ok, db.dumps(mismatches)])
    if not ok and (not prev or prev[0]["ok"]):
        listed = "; ".join(f"{s}: expected {v['expected']:g}, broker has {v['actual']:g}"
                           for s, v in sorted(mismatches.items())[:5])
        _alert("broker_mismatch", "bad", f"mismatch:{broker.name}:{naive_utc(now).isoformat()}",
               f"{broker.name} holdings don't match the lab's record", listed)
    return {"ok": ok, "mismatches": mismatches}


# --- Reducing risk ---------------------------------------------------------------------------------------------


def _cancel_open(brokers: dict[str, Broker], now: datetime, entry_id: str | None = None) -> list[str]:
    """Cancel the lab's orders that aren't final.

    Args:
        brokers: Broker objects.
        now: Time (tz-aware).
        entry_id: Only this sleeve's orders; None cancels all.

    Returns:
        Messages, one per order tried.
    """
    placeholders = ", ".join("?" * len(OPEN_STATUSES))
    args: list[Any] = list(OPEN_STATUSES)
    extra = ""
    if entry_id:
        extra, args = " AND entry_id = ?", [*args, entry_id]
    msgs = []
    for row in db.rows(f"SELECT * FROM broker_orders WHERE status IN ({placeholders}){extra}", args):
        b = brokers.get(row["broker"])
        if b is None or not b.configured():
            continue
        try:
            b.cancel(row["client_id"], row["symbol"])
            state = b.order(row["client_id"], row["symbol"])
            _record_state(row, state, now)
            msgs.append(f"cancelled {row['client_id']} ({state.status})")
        except BrokerError as err:
            msgs.append(f"{row['client_id']}: {err}")
    return msgs


def _flatten_sleeve(sl: dict[str, Any], broker: Broker, now: datetime) -> list[dict[str, Any]]:
    """Sell everything a sleeve holds, bypassing every limit (selling only reduces risk).

    Args:
        sl: Sleeve row.
        broker: Its broker.
        now: Time (tz-aware).

    Returns:
        The orders sent.
    """
    held, _ = sleeve_book(sl, broker.quote)
    stamp = now.astimezone(ZoneInfo("UTC")).strftime("%y%m%d%H%M")
    sent = []
    for s, q in sorted(held.items()):
        qty = q if broker.name == "alpaca" else broker.round_qty(s, q)
        if qty <= 0:
            continue
        row = {"client_id": client_id("k", sl["entry_id"], stamp, s), "entry_id": sl["entry_id"],
               "purpose": "flatten", "as_of": None, "symbol": s, "side": "sell", "qty": qty,
               "tif": "day" if broker.name == "alpaca" else "gtc", "ref_price": None, "late": False}
        sent.append(_send(broker, row, now))
    return sent


def kill(reason: str, actor: str = "user", now: datetime | None = None,
         brokers: dict[str, Broker] | None = None) -> dict[str, Any]:
    """The kill switch: stop all new orders at once, cancel open orders and sell everything every sleeve holds.

    The stop is recorded before any broker is called, so it holds even if a broker can't be reached; cancelling and
    selling are then tried on every configured broker. Lifting it is ``controls.resume`` (typed, with a reason, 24
    hours later).

    Args:
        reason: Why (may be short).
        actor: Who.
        now: Time (tz-aware); None means now.
        brokers: Broker objects; None builds them from the settings.

    Returns:
        ``state`` (the controls after the kill), ``cancelled`` and ``flatten`` (orders sent per sleeve).
    """
    now = now or now_utc()
    controls.record_kill(reason, actor, now)
    brokers = brokers if brokers is not None else make_brokers()
    cancelled = _cancel_open(brokers, now)
    flat: dict[str, list[dict[str, Any]]] = {}
    for sl in sleeves("active"):
        b = brokers.get(sl["broker"])
        if b is not None and b.configured():
            try:
                flat[sl["entry_id"]] = _flatten_sleeve(sl, b, now)
            except BrokerError as err:
                flat[sl["entry_id"]] = [{"status": "error", "message": str(err)}]
    n = sum(len(v) for v in flat.values())
    _alert("broker_kill", "bad", f"kill:{naive_utc(now).isoformat()}", "Kill switch: all paper trading stopped",
           f"{reason or 'no reason given'}. {len(cancelled)} open orders cancelled, {n} sell orders sent to flatten.")
    return {"state": controls.state(now), "cancelled": cancelled, "flatten": flat}


def flatten(entry_id: str, reason: str, actor: str = "user", now: datetime | None = None,
            brokers: dict[str, Broker] | None = None) -> dict[str, Any]:
    """Pause one sleeve and sell what it holds. Takes effect at once.

    Args:
        entry_id: Paper-book entry with a sleeve.
        reason: Why (required).
        actor: Who.
        now: Time (tz-aware); None means now.
        brokers: Broker objects; None builds them from the settings.

    Returns:
        ``cancelled`` and ``orders``.

    Raises:
        KeyError: No sleeve for the entry.
        ValueError: Missing reason.
    """
    now = now or now_utc()
    rows = db.rows("SELECT * FROM broker_sleeves WHERE entry_id = ?", [entry_id])
    if not rows:
        raise KeyError(f"no sleeve for {entry_id}")
    controls.pause(entry_id, reason, actor, now)
    brokers = brokers if brokers is not None else make_brokers()
    b = brokers.get(rows[0]["broker"])
    cancelled = _cancel_open(brokers, now, entry_id)
    orders = _flatten_sleeve(rows[0], b, now) if b is not None and b.configured() else []
    _alert("broker_kill", "info", f"flatten:{entry_id}:{naive_utc(now).isoformat()}",
           f"Sleeve {entry_id} paused and flattened", f"{reason}. {len(orders)} sell orders sent.")
    return {"cancelled": cancelled, "orders": orders}


# --- Reading ---------------------------------------------------------------------------------------------------


def slippage(entry_id: str | None = None) -> list[dict[str, Any]]:
    """Each filled rebalance or catch-up against the raw open the engine would have filled at.

    Args:
        entry_id: Only this sleeve; None for all.

    Returns:
        One row per order with a stored model open: ``client_id, entry_id, purpose, symbol, side, qty, tif, late,
        fill_price, model_price, slippage_bps`` (positive means the fill was worse than the model) and ``model_bps``
        (the half-spread plus slippage the cost model charges for that asset class).
    """
    clauses, args = ["o.purpose IN ('rebalance', 'catchup')", "o.filled_qty > 0"], []
    if entry_id:
        clauses.append("o.entry_id = ?")
        args.append(entry_id)
    rows = db.rows(f"""SELECT o.*, d.next_open FROM broker_orders o
                       JOIN paper_decisions d ON d.entry_id = o.entry_id AND d.as_of = o.as_of
                       WHERE {' AND '.join(clauses)} ORDER BY o.planned_at""", args)
    out = []
    for r in rows:
        try:
            _, sources = _instruments(book.entry(r["entry_id"]))
        except (KeyError, ValueError, FileNotFoundError):
            continue
        source = sources.get(r["symbol"], "yahoo")
        day = pd.Timestamp(_when(r["next_open"]).date(), tz="UTC")
        bar = raw_bar(source, r["symbol"], day)
        if bar is None or not r["avg_price"]:
            continue
        model = bar[0]
        sign = 1.0 if r["side"] == "buy" else -1.0
        preset = COST_PRESETS["crypto_xs" if r["broker"] == "binance" else "etf"]
        out.append({"client_id": r["client_id"], "entry_id": r["entry_id"], "purpose": r["purpose"],
                    "symbol": r["symbol"], "side": r["side"],
                    "qty": r["filled_qty"], "tif": r["tif"], "late": r["late"], "fill_price": r["avg_price"],
                    "model_price": model, "slippage_bps": sign * (r["avg_price"] / model - 1.0) * 1e4,
                    "model_bps": preset.spread_bps / 2 + preset.slippage_bps})
    return out


def status(now: datetime | None = None, brokers: dict[str, Broker] | None = None) -> dict[str, Any]:
    """Everything the Trading Desk shows about broker paper trading. Reads the brokers, never sends an order.

    Args:
        now: Time (tz-aware); None means now.
        brokers: Broker objects; None builds them from the settings.

    Returns:
        ``controls`` (mode, pauses, pending changes, limits), ``brokers`` (per broker: configured, account,
        holdings, latest reconciliation, or an error), ``sleeves`` (with holdings, cash, equity at the latest raw
        closes and the latest decision), ``orders`` (latest 100), ``fills`` (latest 100) and ``slippage`` (per order
        plus a summary).
    """
    now = now or now_utc()
    brokers = brokers if brokers is not None else make_brokers()
    out_brokers = {}
    for name, b in brokers.items():
        row: dict[str, Any] = {"configured": b.configured(), "quote": b.quote}
        if b.configured():
            try:
                a = b.account()
                row.update(account={"equity": a.equity, "cash": a.cash, "currency": a.currency,
                                    "tradable": a.tradable}, holdings=b.holdings())
            except BrokerError as err:
                row["error"] = str(err)
        last = db.rows("SELECT * FROM broker_reconciliations WHERE broker = ? ORDER BY ts DESC LIMIT 1", [name])
        row["reconciliation"] = ({**last[0], "details": json.loads(last[0]["details"] or "{}")} if last else None)
        out_brokers[name] = row
    sl_rows = []
    for sl in sleeves():
        b = brokers.get(sl["broker"])
        quote = b.quote if b else "USD"
        held, cash = sleeve_book(sl, quote)
        try:
            _, sources = _instruments(book.entry(sl["entry_id"]))
        except (KeyError, ValueError, FileNotFoundError):
            sources = {}
        value = {s: q * (_last_close(sources.get(s, "yahoo"), s) or 0.0) for s, q in held.items()}
        decs = book.decisions(sl["entry_id"])
        sl_rows.append({**sl, "holdings": held, "value": value, "cash": cash, "equity": cash + sum(value.values()),
                        "return": (cash + sum(value.values())) / sl["capital"] - 1.0,
                        "last_decision": decs[-1] if decs else None,
                        "state": controls.state(now)["entries"].get(sl["entry_id"], "active")})
    slip = slippage()
    summary = {}
    for name in brokers:
        vals = [r["slippage_bps"] for r in slip if (r["symbol"].endswith("USDT")) == (name == "binance")]
        if vals:
            summary[name] = {"n": len(vals), "mean_bps": sum(vals) / len(vals),
                             "median_bps": sorted(vals)[len(vals) // 2]}
    return {
        "controls": controls.state(now),
        "brokers": out_brokers,
        "sleeves": sl_rows,
        "orders": db.rows("SELECT * FROM broker_orders ORDER BY planned_at DESC LIMIT 100"),
        "fills": db.rows("SELECT * FROM broker_fills ORDER BY filled_at DESC LIMIT 100"),
        "slippage": {"orders": slip, "summary": summary},
    }


__all__ = ["catch_up", "enroll", "execute", "flatten", "kill", "plan_orders", "reconcile", "sleeves", "slippage",
           "status", "sync", "time_in_force"]
