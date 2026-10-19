"""Binance's spot testnet (crypto, paper money).

Notes:

- Base URL ``https://testnet.binance.vision``; keys ``QLAB_BINANCE_TESTNET_KEY`` and ``QLAB_BINANCE_TESTNET_SECRET``
  (created on the testnet site, not on binance.com). Signed requests carry a timestamp and an HMAC-SHA256 signature
  of the query string; the secret itself is never sent.
- **Holdings are balances.** A spot account holds coins, not positions, and a testnet account starts with balances
  the lab didn't buy. ``holdings`` reports each coin's balance under its USDT pair name; reconciliation compares
  changes since a baseline (see ``execution``).
- Quantities round down to the pair's ``LOT_SIZE`` step, and orders below its minimum notional are skipped.
- The testnet lists only a few dozen pairs and is reset from time to time, which shows up as a reconciliation
  mismatch.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from .. import config
from .base import Account, BrokerError, Fill, OrderRequest, OrderState, PaperClient, clean

BASE_URL = "https://testnet.binance.vision"
KEY, SECRET = "QLAB_BINANCE_TESTNET_KEY", "QLAB_BINANCE_TESTNET_SECRET"
QUOTE = "USDT"
STATUS = {"NEW": "new", "PARTIALLY_FILLED": "partially_filled", "FILLED": "filled", "CANCELED": "canceled",
          "PENDING_CANCEL": "accepted", "REJECTED": "rejected", "EXPIRED": "expired", "EXPIRED_IN_MATCH": "expired"}


class BinanceTestnet:
    """A Binance spot testnet account.

    Attributes:
        name: ``"binance"``.
        asset_class: ``"crypto"``.
        quote: ``"USDT"``.
    """

    name = "binance"
    asset_class = "crypto"
    quote = QUOTE

    def __init__(self, client: httpx.Client | None = None, clock=time.time) -> None:
        """Read the keys and prepare the client.

        Args:
            client: An HTTP client to use (tests pass a mock); None opens one.
            clock: Returns Unix seconds, for request timestamps.
        """
        self._key, self._secret = config.setting(KEY) or "", config.setting(SECRET) or ""
        self.http = PaperClient(BASE_URL, client)
        self._clock = clock
        self._rules: dict[str, dict[str, float]] = {}

    def configured(self) -> bool:
        """Whether both testnet keys are set."""
        return bool(self._key and self._secret)

    def _call(self, method: str, path: str, params: dict[str, Any] | None = None, signed: bool = True) -> Any:
        """Send a request, signing it when needed, and decode the JSON answer.

        Args:
            method: HTTP method.
            path: API path.
            params: Parameters (sent in the query string).
            signed: Add the timestamp, the signature and the key header.

        Returns:
            The decoded JSON.

        Raises:
            BrokerError: Missing keys, a network failure or an error status (with Binance's message, never a key).
        """
        params = dict(params or {})
        headers = {}
        if signed:
            if not self.configured():
                raise BrokerError(f"Binance testnet keys are missing: set {KEY} and {SECRET} in {config.env_file()}")
            params.update(timestamp=int(self._clock() * 1000), recvWindow=5000)
            query = urlencode(params)
            sig = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            params = None
            path = f"{path}?{query}&signature={sig}"
            headers["X-MBX-APIKEY"] = self._key
        res = self.http.request(method, path, params=params, headers=headers)
        if res.status_code >= 400:
            try:
                msg = res.json().get("msg", res.text)
            except ValueError:
                msg = res.text
            raise BrokerError(clean(f"Binance testnet {method} {path.split('?')[0]}: HTTP {res.status_code} "
                                    f"{str(msg)[:200]}", self._key, self._secret))
        return res.json()

    def _rule(self, symbol: str) -> dict[str, float]:
        """The pair's lot step, minimum quantity and minimum notional (cached).

        Args:
            symbol: Pair, e.g. ``BTCUSDT``.

        Returns:
            ``{"step", "min_qty", "min_notional"}``.

        Raises:
            BrokerError: The testnet doesn't list the pair.
        """
        if symbol not in self._rules:
            info = self._call("GET", "/api/v3/exchangeInfo", {"symbol": symbol}, signed=False)
            filters = {f["filterType"]: f for f in info["symbols"][0]["filters"]}
            lot = filters.get("LOT_SIZE", {})
            notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
            self._rules[symbol] = {"step": float(lot.get("stepSize", "0.00000001")),
                                   "min_qty": float(lot.get("minQty", "0")),
                                   "min_notional": float(notional.get("minNotional", "0"))}
        return self._rules[symbol]

    def account(self) -> Account:
        """The account's USDT cash; equity here is USDT plus the coins the lab holds, valued by ``execution``.

        Returns:
            The account with ``equity`` equal to its USDT balance.

        Raises:
            BrokerError: The request failed.
        """
        a = self._call("GET", "/api/v3/account")
        usdt = next((float(b["free"]) + float(b["locked"]) for b in a["balances"] if b["asset"] == QUOTE), 0.0)
        return Account(equity=usdt, cash=usdt, currency=QUOTE, tradable=bool(a.get("canTrade", True)))

    def holdings(self) -> dict[str, float]:
        """Coin balances as USDT pairs.

        Returns:
            ``{"<COIN>USDT": balance}`` for every coin with a balance, USDT itself left out.

        Raises:
            BrokerError: The request failed.
        """
        a = self._call("GET", "/api/v3/account")
        out = {}
        for b in a["balances"]:
            qty = float(b["free"]) + float(b["locked"])
            if b["asset"] != QUOTE and qty > 0:
                out[b["asset"] + QUOTE] = qty
        return out

    def _state(self, o: dict[str, Any], fills: list[dict[str, Any]]) -> OrderState:
        """Turn a Binance order and its trades into an ``OrderState``.

        Args:
            o: Order JSON.
            fills: Trades of the order (``fills`` of a FULL response or ``myTrades`` rows).

        Returns:
            The state.
        """
        qty = float(o.get("executedQty") or 0.0)
        quote = float(o.get("cummulativeQuoteQty") or 0.0)
        out = OrderState(client_id=o["clientOrderId"], broker_id=str(o["orderId"]),
                         status=STATUS.get(o["status"], "new"), filled_qty=qty,
                         avg_price=quote / qty if qty > 0 else None)
        for f in fills:
            tid = f.get("tradeId", f.get("id"))
            out.fills.append(Fill(fill_id=f"{o['symbol']}:{tid}", qty=float(f["qty"]), price=float(f["price"]),
                                  fee=float(f.get("commission", 0.0)), fee_asset=f.get("commissionAsset", ""),
                                  filled_at=str(f.get("time", o.get("transactTime", "")))))
        return out

    def submit(self, order: OrderRequest) -> OrderState:
        """Send a market order (time in force doesn't apply to market orders on Binance).

        Args:
            order: The order; ``qty`` already rounded to the lot step.

        Returns:
            Its state with fills. A refusal by Binance (balance, filters, an unlisted pair) comes back as status
            ``rejected`` with the message.

        Raises:
            BrokerError: Missing keys or a network failure.
        """
        params = {"symbol": order.symbol, "side": order.side.upper(), "type": "MARKET",
                  "quantity": _fmt(order.qty), "newClientOrderId": order.client_id, "newOrderRespType": "FULL"}
        try:
            o = self._call("POST", "/api/v3/order", params)
        except BrokerError as err:
            if "HTTP 4" in str(err):
                return OrderState(client_id=order.client_id, broker_id="", status="rejected", message=str(err))
            raise
        o.setdefault("symbol", order.symbol)
        return self._state(o, o.get("fills", []))

    def order(self, client_id: str, symbol: str) -> OrderState:
        """The state of an order the lab sent, with its trades.

        Args:
            client_id: The lab's id.
            symbol: Pair.

        Returns:
            The state.

        Raises:
            BrokerError: The request failed or the order is unknown.
        """
        o = self._call("GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_id})
        trades = self._call("GET", "/api/v3/myTrades", {"symbol": symbol, "orderId": o["orderId"]})
        o.setdefault("symbol", symbol)
        return self._state(o, trades)

    def cancel(self, client_id: str, symbol: str) -> None:
        """Cancel an open order.

        Args:
            client_id: The lab's id.
            symbol: Pair.

        Raises:
            BrokerError: The order is unknown or already final.
        """
        self._call("DELETE", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_id})

    def round_qty(self, symbol: str, qty: float) -> float:
        """Round down to the pair's lot step; below the minimum quantity becomes zero.

        Args:
            symbol: Pair.
            qty: Coins wanted (positive).

        Returns:
            Coins.
        """
        r = self._rule(symbol)
        step = Decimal(str(r["step"]))
        out = float((Decimal(str(max(qty, 0.0))) / step).to_integral_value(ROUND_DOWN) * step)
        return out if out >= r["min_qty"] else 0.0

    def min_notional(self, symbol: str) -> float:
        """The pair's minimum order value in USDT.

        Args:
            symbol: Pair.

        Returns:
            USDT.
        """
        return self._rule(symbol)["min_notional"]


def _fmt(x: float) -> str:
    """A quantity as Binance wants it: plain decimal, no exponent, no trailing zeros.

    Args:
        x: Quantity.

    Returns:
        Text such as ``"0.00123"``.
    """
    s = f"{Decimal(str(x)):f}"
    return s.rstrip("0").rstrip(".") if "." in s else s
