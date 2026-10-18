"""The lockbox: the most recent stretch of data, kept out of research and looked at once per question.

Design: one fixed date for the whole lab (``lockbox_from`` in the gauntlet thresholds). Everything before it
is research data; everything from it on is sealed. A fixed date beats a rolling "last two years" window, which
would quietly turn yesterday's sealed data into today's research data.

The ledger is the source of truth for whether a lockbox is spent: a research question's lockbox is spent as
soon as any trial on that question covered a sealed date, whether it was the gauntlet opening it or a Backtest
Studio run that peeked. Peeking and opening are the same act, so they cost the same.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from . import db
from .timeutil import naive_utc

DEFAULT_FROM = "2024-10-01"


def sealed_from() -> pd.Timestamp:
    """First sealed date, from the thresholds in force.

    Returns:
        A UTC timestamp at midnight.
    """
    from .gauntlet import thresholds  # thresholds own the value; imported here to keep layers one-way

    _, th = thresholds.current()
    return pd.Timestamp(str(th.get("lockbox_from", DEFAULT_FROM)), tz="UTC")


def research_end() -> str:
    """Last research date, the day before the lockbox.

    Returns:
        ``YYYY-MM-DD``.
    """
    return str((sealed_from() - pd.Timedelta(days=1)).date())


def clamp_end(end: str | date | None) -> str:
    """Pull an end date back so a run stays inside the research period.

    Args:
        end: Requested last date. None means "as late as possible".

    Returns:
        The earlier of ``end`` and the last research date, as ``YYYY-MM-DD``.
    """
    last = research_end()
    return last if end is None or str(end)[:10] > last else str(end)[:10]


def spent(question: str) -> bool:
    """Whether a question's lockbox has been looked at.

    Args:
        question: Research question, e.g. ``"trend_sma:SPY:1d"``.

    Returns:
        True when any recorded trial on the question ended on or after the sealed date.
    """
    n = db.scalar(
        "SELECT count(*) FROM trials WHERE question = ? AND end_date >= ? AND source <> 'calibration'",
        [question, sealed_from().date()],
    )
    return bool(n)


def spent_questions() -> list[dict[str, Any]]:
    """Every question whose lockbox is spent, and how.

    Returns:
        Rows ``question, first_peek, n_trials, opened_by_card`` ordered by question. ``opened_by_card`` is set
        when the gauntlet opened it; otherwise a Studio run peeked. Paper-book trials (``paper:`` questions)
        are left out: they score forward days and have no lockbox of their own.
    """
    return db.rows(
        """SELECT t.question, min(t.created_at) AS first_peek, count(*) AS n_trials,
                  any_value(u.card_id) AS opened_by_card
           FROM trials t LEFT JOIN lockbox_uses u ON u.question = t.question
           WHERE t.end_date >= ? AND t.source NOT IN ('calibration', 'paper')
           GROUP BY t.question ORDER BY t.question""",
        [sealed_from().date()],
    )


def record_opening(family: str, question: str, card_id: str, reason: str) -> None:
    """Log that the gauntlet opened a lockbox for a report card.

    Args:
        family: Strategy family.
        question: Research question.
        card_id: Report card that was judged on the lockbox.
        reason: Why it was opened, in the person's words.
    """
    with db.connect() as con:
        con.execute(
            "INSERT INTO lockbox_uses (family, question, card_id, used_at, reason) VALUES (?, ?, ?, ?, ?)",
            [family, question, card_id, naive_utc(), reason],
        )
