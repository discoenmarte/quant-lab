"""Time helpers. Storage is always UTC; the UI shows exchange time next to it.

One of the six live-vs-backtest defects in Vidovich & Zerjal (2026) was UTC read as New York time, so the
lab never stores local time and never guesses a timezone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .config import EXCHANGE_TZ


def now_utc() -> datetime:
    """Current time.

    Returns:
        A timezone-aware datetime in UTC.
    """
    return datetime.now(UTC)


def clocks() -> dict[str, str]:
    """Current time in UTC and in the exchange's timezone.

    Returns:
        ``{"utc": ISO time, "exchange": ISO time, "exchange_tz": IANA name}``.
    """
    now = now_utc()
    return {
        "utc": now.isoformat(timespec="seconds"),
        "exchange": now.astimezone(ZoneInfo(EXCHANGE_TZ)).isoformat(timespec="seconds"),
        "exchange_tz": EXCHANGE_TZ,
    }


def naive_utc(dt: datetime | None = None) -> datetime:
    """UTC time without tzinfo, which is how DuckDB TIMESTAMP columns store it.

    Args:
        dt: Timezone-aware datetime to convert. None means now.

    Returns:
        A naive datetime holding the UTC wall-clock time.
    """
    return (dt or now_utc()).astimezone(UTC).replace(tzinfo=None)
