"""Time helpers. All storage is UTC; the UI always shows exchange time next to UTC.

(One of the six live-vs-backtest defects in Vidovich & Zerjal, 2026, was UTC read as New York time.)
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .config import EXCHANGE_TZ


def now_utc() -> datetime:
    return datetime.now(UTC)


def clocks() -> dict[str, str]:
    now = now_utc()
    return {
        "utc": now.isoformat(timespec="seconds"),
        "exchange": now.astimezone(ZoneInfo(EXCHANGE_TZ)).isoformat(timespec="seconds"),
        "exchange_tz": EXCHANGE_TZ,
    }


def naive_utc(dt: datetime | None = None) -> datetime:
    """UTC timestamp without tzinfo, which is how DuckDB TIMESTAMP columns store it."""
    return (dt or now_utc()).astimezone(UTC).replace(tzinfo=None)
