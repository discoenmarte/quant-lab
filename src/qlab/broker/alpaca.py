"""Alpaca's paper trading API (US stocks and ETFs).

Notes:

- Base URL ``https://paper-api.alpaca.markets``, fixed. Keys are paper keys from the Alpaca dashboard's paper account,
  read from ``QLAB_ALPACA_KEY``/``QLAB_ALPACA_SECRET``, or the common ``ALPACA_KEY``/``ALPACA_SECRET`` or Alpaca's
  own ``APCA_API_KEY_ID``/``APCA_API_SECRET_KEY`` (environment, ``data/lab.env`` or the repository's ``.env``). They
  are sent as the ``APCA-API-KEY-ID`` and ``APCA-API-SECRET-KEY`` headers. Live keys (Alpaca prefixes them ``AK``,
  paper keys ``PK``) are refused, and so is an ``ALPACA_ENDPOINT`` that isn't the paper host; the adapter never uses
  that setting as its address.
- **Whole shares.** Fractional orders can't join the opening auction, so quantities round down to whole shares.
- **Fills.** An Alpaca order reports its cumulative filled quantity and average price, not each execution, so its
  ``OrderState`` has no ``fills``. ``execution.sync`` records each increase as one fill, at the price implied by the
  change in the average, from the quantities it stored last time (an in-memory record wouldn't survive between
  nightly runs).
- Commission is zero on Alpaca; the tiny regulatory fees on sells aren't in the API and are ignored.
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import config
from .base import Account, BrokerError, OrderRequest, OrderState, PaperClient, clean

BASE_URL = "https://paper-api.alpaca.markets"
KEY, SECRET = "QLAB_ALPACA_KEY", "QLAB_ALPACA_SECRET"
KEY_NAMES = (KEY, "ALPACA_KEY", "APCA_API_KEY_ID")
SECRET_NAMES = (SECRET, "ALPACA_SECRET", "APCA_API_SECRET_KEY")


def _first(names: tuple[str, ...]) -> str:
    """The first of several setting names that has a value.

    Args:
        names: Setting names, in order of preference.

    Returns:
        The value, or an empty string.
    """
    return next((v for n in names if (v := config.setting(n))), "")
STATUS = {
    "new": "new", "pending_new": "new", "accepted": "accepted", "accepted_for_bidding": "accepted",
    "pending_cancel": "accepted", "pending_replace": "accepted", "calculated": "accepted", "held": "accepted",
    "partially_filled": "partially_filled", "filled": "filled", "canceled": "canceled", "expired": "expired",
    "done_for_day": "expired", "replaced": "canceled", "rejected": "rejected", "stopped": "accepted",
    "suspended": "accepted",
}


class AlpacaPaper:
    """An Alpaca paper account.

    Attributes:
        name: ``"alpaca"``.
        asset_class: ``"equity"``: the instruments the lab sends here.
        quote: ``"USD"``.
    """

    name = "alpaca"
    asset_class = "equity"
    quote = "USD"

    def __init__(self, client: httpx.Client | None = None) -> None:
        """Read the keys and prepare the client.

        Args:
            client: An HTTP client to use (tests pass a mock); None opens one.
        """
        self._key, self._secret = _first(KEY_NAMES), _first(SECRET_NAMES)
        self.http = PaperClient(BASE_URL, client)
        endpoint = config.setting("ALPACA_ENDPOINT") or ""
        host = urlsplit(endpoint).hostname
        self._problem = ""
        if endpoint and host != urlsplit(BASE_URL).hostname:
            self._problem = f"ALPACA_ENDPOINT points at {host}, not the paper API; the lab trades paper only before M8"
        elif self._key.startswith("AK"):
            self._problem = "the Alpaca key is a live key (prefix AK); use the paper account's keys (prefix PK)"

    def configured(self) -> bool:
        """Whether both paper keys are set."""
        return bool(self._key and self._secret)

    def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        """Send an authenticated request and turn errors into ``BrokerError``.

        Args:
            method: HTTP method.
            path: API path.
            **kw: ``params`` or ``json``.

        Returns:
            A successful response.

        Raises:
            BrokerError: Missing keys, a network failure or an error status (with Alpaca's message, never the keys).
        """
        if self._problem:
            raise BrokerError(self._problem)
        if not self.configured():
            raise BrokerError(f"Alpaca paper keys are missing: set {KEY} and {SECRET} (or ALPACA_KEY and "
                              f"ALPACA_SECRET) in {config.env_file()} or the repository's .env")
        res = self.http.request(method, path, headers={"APCA-API-KEY-ID": self._key,
                                                       "APCA-API-SECRET-KEY": self._secret}, **kw)
        if res.status_code >= 400:
            try:
                msg = res.json().get("message", res.text)
            except ValueError:
                msg = res.text
            raise BrokerError(clean(f"Alpaca {method} {path}: HTTP {res.status_code} {str(msg)[:200]}",
                                    self._key, self._secret))
        return res

    def account(self) -> Account:
        """Equity, cash and whether trading is allowed.

        Returns:
            The account.

        Raises:
            BrokerError: The request failed.
        """
        a = self._call("GET", "/v2/account").json()
        return Account(equity=float(a["equity"]), cash=float(a["cash"]), currency=a.get("currency", "USD"),
                       tradable=not (a.get("trading_blocked") or a.get("account_blocked")))

    def holdings(self) -> dict[str, float]:
        """Shares held per symbol.

        Returns:
            ``{symbol: shares}``; short positions are negative.

        Raises:
            BrokerError: The request failed.
        """
        return {p["symbol"]: float(p["qty"]) for p in self._call("GET", "/v2/positions").json()}

    def _state(self, o: dict[str, Any]) -> OrderState:
        """Turn an Alpaca order into an ``OrderState`` (cumulative quantity and average price, no per-fill list).

        Args:
            o: Order JSON.

        Returns:
            The state.
        """
        qty = float(o.get("filled_qty") or 0.0)
        avg = float(o["filled_avg_price"]) if o.get("filled_avg_price") else None
        return OrderState(client_id=o["client_order_id"], broker_id=o["id"], status=STATUS.get(o["status"], "new"),
                          filled_qty=qty, avg_price=avg, updated_at=o.get("filled_at") or o.get("updated_at") or "")

    def submit(self, order: OrderRequest) -> OrderState:
        """Send a market order.

        Args:
            order: The order; ``qty`` must be whole shares.

        Returns:
            Its state. A refusal by Alpaca (buying power, an unknown symbol, a closed auction window) comes back as
            status ``rejected`` with the message, not as an exception.

        Raises:
            BrokerError: Missing keys or a network failure.
        """
        body = {"symbol": order.symbol, "qty": f"{order.qty:g}", "side": order.side, "type": "market",
                "time_in_force": order.tif, "client_order_id": order.client_id}
        try:
            return self._state(self._call("POST", "/v2/orders", json=body).json())
        except BrokerError as err:
            if "HTTP 4" in str(err):
                return OrderState(client_id=order.client_id, broker_id="", status="rejected", message=str(err))
            raise

    def order(self, client_id: str, symbol: str) -> OrderState:
        """The state of an order the lab sent.

        Args:
            client_id: The lab's id.
            symbol: Instrument (unused by Alpaca's lookup).

        Returns:
            The state with any new fills.

        Raises:
            BrokerError: The request failed or the order is unknown.
        """
        return self._state(self._call("GET", "/v2/orders:by_client_order_id",
                                      params={"client_order_id": client_id}).json())

    def cancel(self, client_id: str, symbol: str) -> None:
        """Cancel an open order.

        Args:
            client_id: The lab's id.
            symbol: Instrument (unused).

        Raises:
            BrokerError: The order is unknown or can't be cancelled.
        """
        o = self._call("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_id}).json()
        self._call("DELETE", f"/v2/orders/{o['id']}")

    def round_qty(self, symbol: str, qty: float) -> float:
        """Round down to whole shares.

        Args:
            symbol: Instrument.
            qty: Shares wanted.

        Returns:
            Whole shares, at least zero for positive input.
        """
        return float(math.floor(qty + 1e-9)) if qty >= 0 else -float(math.floor(-qty + 1e-9))

    def min_notional(self, symbol: str) -> float:
        """Smallest order value: one dollar.

        Args:
            symbol: Instrument.

        Returns:
            1.0.
        """
        return 1.0
