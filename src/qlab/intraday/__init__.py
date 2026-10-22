"""Intraday mode (M9): rules that enter and leave within one UTC day, on hourly crypto bars.

Decision 0015. The roadmap's US intraday library needs consolidated minute data (about $29-99 a month); the free
Binance archive has every hourly bar of BTC and ETH since 2017, so the library starts there:

- ``rules``: intraday momentum (the first hours of the day predict its last hours) and an opening-range breakout,
  each with an optional selector that trades only after a volatile day.
- ``synthetic``: hourly markets with an intraday volatility profile, for calibrating the gauntlet on these rules.

They are ordinary registered strategies, so the gauntlet, the ledger and the lockbox treat them like any other.
"""
