"""What every broker adapter provides, and the guard that keeps them on paper.

Design:

- **Paper only, structurally.** Each adapter has one fixed base URL, and every request goes through
  ``PaperClient.request``, which refuses any host outside ``PAPER_HOSTS`` (Alpaca's paper API and Binance's spot
  testnet). There is no setting, flag or environment variable that points an adapter at a live endpoint; going live
  is the go-live gate (M8), which doesn't exist yet.
- **Credentials stay in the process.** Keys come from the environment or ``<data>/lab.env`` (``config.setting``).
  They are sent only in request headers or as an HMAC signature, and never logged, stored, returned or put in an
  error message.
- **A small interface.** The lab needs an account's equity, its holdings, a market order, an order's state with its
  fills, cancellation, and the instrument's lot size. Everything else (limits, sizing, the kill switch) lives in
  ``controls`` and ``execution``, outside the adapters and outside any strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

PAPER_HOSTS = frozenset({"paper-api.alpaca.markets", "testnet.binance.vision"})


class BrokerError(RuntimeError):
    """A broker refused a request, couldn't be reached, or isn't configured. The message never holds a secret."""


@dataclass(frozen=True)
class OrderRequest:
    """A market order the lab wants to send.

    Attributes:
        client_id: The lab's id for the order; brokers refuse a second order with the same id.
        symbol: Instrument as the broker names it (``SPY``, ``BTCUSDT``).
        side: ``"buy"`` or ``"sell"``.
        qty: Units to trade (shares or coins), already rounded to the broker's lot size.
        tif: Time in force: ``"opg"`` (Alpaca's market-on-open auction), ``"day"`` or ``"gtc"``.
    """

    client_id: str
    symbol: str
    side: str
    qty: float
    tif: str = "day"


@dataclass
class Fill:
    """One execution of an order.

    Attributes:
        fill_id: The broker's id for the execution (unique per broker).
        qty: Units filled.
        price: Price per unit.
        fee: Commission charged, in ``fee_asset``.
        fee_asset: Currency or coin of the fee.
        filled_at: ISO time of the execution, UTC.
    """

    fill_id: str
    qty: float
    price: float
    fee: float = 0.0
    fee_asset: str = ""
    filled_at: str = ""


@dataclass
class OrderState:
    """What the broker says about one order.

    Attributes:
        client_id: The lab's id.
        broker_id: The broker's id.
        status: ``new``, ``accepted``, ``partially_filled``, ``filled``, ``canceled``, ``rejected`` or ``expired``.
        filled_qty: Units filled so far.
        avg_price: Average fill price, or None before any fill.
        fills: Executions known so far (empty for brokers that report only cumulative fills, such as Alpaca).
        message: The broker's reason for a rejection, if any.
        updated_at: ISO time of the latest fill or update the broker reported, UTC; empty when unknown.
    """

    client_id: str
    broker_id: str
    status: str
    filled_qty: float = 0.0
    avg_price: float | None = None
    fills: list[Fill] = field(default_factory=list)
    message: str = ""
    updated_at: str = ""

    @property
    def final(self) -> bool:
        """Whether the order can't change any more."""
        return self.status in FINAL


FINAL = {"filled", "canceled", "rejected", "expired"}


@dataclass(frozen=True)
class Account:
    """A paper account's headline numbers.

    Attributes:
        equity: Total value in the account currency.
        cash: Cash in the account currency.
        currency: ``USD`` (Alpaca) or ``USDT`` (Binance).
        tradable: False when the broker has blocked trading.
    """

    equity: float
    cash: float
    currency: str
    tradable: bool = True


class Broker(Protocol):
    """A paper broker the lab can trade through."""

    name: str
    asset_class: str
    quote: str

    def configured(self) -> bool:
        """Whether both keys are set."""

    def account(self) -> Account:
        """Equity, cash and whether trading is allowed."""

    def holdings(self) -> dict[str, float]:
        """Units held per symbol (shares for Alpaca; base-coin balances as ``<COIN>USDT`` for Binance)."""

    def submit(self, order: OrderRequest) -> OrderState:
        """Send a market order."""

    def order(self, client_id: str, symbol: str) -> OrderState:
        """The current state of an order the lab sent, with its fills."""

    def cancel(self, client_id: str, symbol: str) -> None:
        """Cancel an open order."""

    def round_qty(self, symbol: str, qty: float) -> float:
        """Round a quantity down to the instrument's lot size."""

    def min_notional(self, symbol: str) -> float:
        """Smallest order value the broker accepts for the instrument."""


class PaperClient:
    """An HTTP client that can only talk to a paper or testnet host."""

    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float = 20.0) -> None:
        """Check the base URL and open (or adopt) a client.

        Args:
            base_url: Scheme and host of the broker's paper API.
            client: An existing client (tests pass one with a mock transport); None opens one on first use.
            timeout: Seconds per request.

        Raises:
            BrokerError: The host isn't a paper host.
        """
        _check_host(base_url)
        self.base_url = base_url.rstrip("/")
        self._client, self._timeout = client, timeout  # opened on first use: an unconfigured broker opens nothing

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                json: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
                content: str | None = None) -> httpx.Response:
        """Send one request to the paper host.

        Args:
            method: HTTP method.
            path: Path below the base URL, starting with ``/``.
            params: Query parameters.
            json: JSON body.
            headers: Extra headers (credentials).
            content: Raw body (form-encoded, already signed).

        Returns:
            The response, whatever its status.

        Raises:
            BrokerError: The URL left the paper host, or the request failed on the network. The message holds the
                path and the error type only.
        """
        url = self.base_url + path
        _check_host(url)
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        try:
            return self._client.request(method, url, params=params, json=json, headers=headers, content=content)
        except httpx.HTTPError as err:  # the request object can carry headers; report the type only
            raise BrokerError(f"{method} {path} failed ({type(err).__name__})") from None


def _check_host(url: str) -> None:
    """Refuse any URL whose host isn't a paper or testnet endpoint.

    Args:
        url: Absolute URL.

    Raises:
        BrokerError: The host isn't in ``PAPER_HOSTS`` or the scheme isn't https.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in PAPER_HOSTS:
        raise BrokerError(f"refusing {parts.hostname or url}: only paper and testnet hosts are allowed before M8")


def clean(text: str, *secrets: str) -> str:
    """Remove secrets from a piece of text before it goes into an error or a log.

    Args:
        text: Any text.
        *secrets: Values to hide.

    Returns:
        The text with each non-empty secret replaced by ``***``.
    """
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text
