"""Session times for daily bars: when a bar opens, when its close is final, and which session comes next.

The paper book needs wall-clock times, not just dates, to keep decisions ahead of outcomes. Daily bars are keyed
by session date at 00:00 UTC (see ``data.bars``), so the times come from the asset class:

- **Equities** (US ETFs): open 09:30 and close 16:00 New York time. A close counts as final 30 minutes later,
  because Yahoo updates the last price for closing-auction prints for a few minutes after the bell.
- **Crypto** (Binance daily candles): open at 00:00 UTC of the date, close at 00:00 UTC of the next date. The
  archive publishes only complete days, so a stored crypto bar is final when its close has passed.

Calendars are approximated by weekdays for equities and every day for crypto. Exchange holidays are not modelled:
on a holiday the next weekday is assumed to trade, which can only make the book refuse a decision it could have
taken (conservative), never accept one it shouldn't.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from ..config import EXCHANGE_TZ

EQUITY_OPEN = pd.Timedelta(hours=9, minutes=30)
EQUITY_CLOSE = pd.Timedelta(hours=16)
EQUITY_SETTLE = pd.Timedelta(minutes=30)


def _days(days: pd.DatetimeIndex | pd.Timestamp) -> pd.DatetimeIndex:
    """Session dates as a naive midnight index.

    Args:
        days: Session dates at 00:00 UTC (tz-aware or naive), one value or an index.

    Returns:
        A naive ``DatetimeIndex`` normalised to midnight.
    """
    idx = pd.DatetimeIndex([days] if isinstance(days, pd.Timestamp) else days)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.normalize()


def _equity_time(days: pd.DatetimeIndex, offset: pd.Timedelta) -> pd.DatetimeIndex:
    """A New York wall-clock time on each date, in UTC.

    Args:
        days: Naive session dates.
        offset: Time after local midnight.

    Returns:
        Tz-aware UTC times.
    """
    return (days + offset).tz_localize(EXCHANGE_TZ).tz_convert("UTC")


def opens(days: pd.DatetimeIndex | pd.Timestamp, asset_class: str) -> pd.DatetimeIndex:
    """When each date's session opens.

    Args:
        days: Session dates.
        asset_class: ``"crypto"``; anything else is treated as a US equity.

    Returns:
        Tz-aware UTC times.
    """
    d = _days(days)
    if asset_class == "crypto":
        return d.tz_localize("UTC")
    return _equity_time(d, EQUITY_OPEN)


def closes(days: pd.DatetimeIndex | pd.Timestamp, asset_class: str) -> pd.DatetimeIndex:
    """When each date's session closes (the outcome of the bar is known from here on).

    Args:
        days: Session dates.
        asset_class: ``"crypto"``; anything else is treated as a US equity.

    Returns:
        Tz-aware UTC times.
    """
    d = _days(days)
    if asset_class == "crypto":
        return (d + pd.Timedelta(days=1)).tz_localize("UTC")
    return _equity_time(d, EQUITY_CLOSE)


def finals(days: pd.DatetimeIndex | pd.Timestamp, asset_class: str) -> pd.DatetimeIndex:
    """When each date's stored close can be trusted as final.

    Args:
        days: Session dates.
        asset_class: ``"crypto"``; anything else is treated as a US equity.

    Returns:
        Tz-aware UTC times: the close, plus 30 minutes for equities.
    """
    c = closes(days, asset_class)
    return c if asset_class == "crypto" else c + EQUITY_SETTLE


def final_mask(days: pd.DatetimeIndex, classes: Sequence[str], now: datetime) -> np.ndarray:
    """Which dates of a calendar have a final close at a given time.

    A date of a multi-asset panel is final only when every member's bar is (the latest member close).

    Args:
        days: Session dates of the calendar.
        classes: Asset class of each member (one entry for a single instrument).
        now: Tz-aware time of the check.

    Returns:
        Boolean per date.
    """
    if len(days) == 0:
        return np.zeros(0, dtype=bool)
    latest = pd.DataFrame({c: finals(days, c) for c in set(classes)}).max(axis=1)
    return np.array(latest <= pd.Timestamp(now).tz_convert("UTC"), dtype=bool)


def next_session(day: pd.Timestamp, classes: Sequence[str]) -> pd.Timestamp:
    """The session date after ``day`` on a calendar.

    Args:
        day: Session date.
        classes: Asset class of each member; any equity member puts the calendar on weekdays.

    Returns:
        The next date at 00:00 UTC.
    """
    d = _days(day)[0]
    step = pd.offsets.BDay(1) if any(c != "crypto" for c in classes) else pd.Timedelta(days=1)
    return (d + step).normalize().tz_localize("UTC")


def first_open(day: pd.Timestamp, classes: Sequence[str]) -> datetime:
    """The earliest member open on a session date: when the first fill of a decision can happen.

    Args:
        day: Session date.
        classes: Asset class of each member.

    Returns:
        A tz-aware UTC datetime.
    """
    return min(opens(day, c)[0] for c in set(classes)).to_pydatetime()


def first_close(day: pd.Timestamp, classes: Sequence[str]) -> datetime:
    """The earliest member close on a session date: from then on part of that day's outcome is known.

    Args:
        day: Session date.
        classes: Asset class of each member.

    Returns:
        A tz-aware UTC datetime.
    """
    return min(closes(day, c)[0] for c in set(classes)).to_pydatetime()


def hours_between(start: datetime, end: datetime) -> float:
    """Signed hours from ``start`` to ``end``.

    Args:
        start: A datetime; naive values are read as UTC.
        end: A datetime; naive values are read as UTC.

    Returns:
        Hours, negative when ``end`` is earlier.
    """
    def aware(t: datetime) -> datetime:
        return t if t.tzinfo else t.replace(tzinfo=UTC)

    return (aware(end) - aware(start)) / timedelta(hours=1)
