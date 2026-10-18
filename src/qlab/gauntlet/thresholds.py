"""Gauntlet thresholds, versioned in the database.

Thresholds are fixed *before* testing. Changing them creates a new version with a written reason, every report
card records the version it was judged against, and earlier cards are never re-judged.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from .. import audit, db
from ..lockbox import DEFAULT_FROM
from ..timeutil import naive_utc

DEFAULT: dict[str, Any] = {
    "lockbox_from": DEFAULT_FROM,  # first sealed date for the whole lab (ISO date)
    "min_net_sharpe": 0.0,         # costs: annual edge Sharpe after costs must exceed this
    "wf_min_oos_sharpe": 0.0,      # walk-forward: out-of-sample edge Sharpe must exceed this
    "wf_min_ratio": 0.5,           # walk-forward: out-of-sample at least this share of in-sample
    "plateau_min": 0.6,            # plateau: share of neighbouring settings that also work
    "perm_max_p": 0.01,            # permutation: p-value must be at most this
    "n_perm": 500,                 # permutation: number of shuffled histories
    "n_boot": 1000,                # bootstrap: number of resampled paths
    "boot_block": 20,              # bootstrap: mean block length in days
    "dd_budget": 0.25,             # bootstrap: drawdown the recommended size keeps the 95th percentile within
    "dsr_min": 0.95,               # significance: Deflated Sharpe probability must reach this
    "pbo_max": 0.2,                # significance: probability of backtest overfitting must stay at or below this
    "fdr_prior": 0.05,             # significance: prior share of tested ideas that are real (for the FDR)
    "fdr_sr1": 0.5,                # significance: annual edge Sharpe of a real idea (for the FDR)
    "regime_min_share": 0.6,       # regimes: share of regimes with a positive net return
    "max_corr": 0.5,               # diversification: correlation to the validated portfolio
    "grid_max": 300,               # parameter grid: most configurations evaluated per run
}


def _validate(key: str, value: Any) -> None:
    """Check one threshold value.

    Args:
        key: Threshold name.
        value: Proposed value.

    Raises:
        ValueError: Unknown key, a non-number, or a ``lockbox_from`` that isn't an ISO date.
    """
    if key not in DEFAULT:
        raise ValueError(f"unknown threshold: {key}")
    if key == "lockbox_from":
        try:
            date.fromisoformat(str(value))
        except ValueError as err:
            raise ValueError("lockbox_from must be a date, YYYY-MM-DD") from err
    elif isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number")


def current() -> tuple[int, dict[str, Any]]:
    """The thresholds in force, creating version 1 from the defaults on first use.

    Returns:
        ``(version, thresholds)``. Keys added to ``DEFAULT`` after a version was saved take their default.
    """
    rows = db.rows("SELECT version, config FROM thresholds ORDER BY version DESC LIMIT 1")
    if rows:
        return int(rows[0]["version"]), {**DEFAULT, **json.loads(rows[0]["config"])}
    with db.connect() as con:
        con.execute(
            "INSERT INTO thresholds (version, created_at, reason, config) VALUES (1, ?, ?, ?) ON CONFLICT DO NOTHING",
            [naive_utc(), "initial defaults from the roadmap", db.dumps(DEFAULT)],
        )
    return 1, dict(DEFAULT)


def update(changes: dict[str, Any], reason: str, actor: str = "user") -> int:
    """Create a new threshold version. Cards already judged keep the version they were judged with.

    Args:
        changes: Keys of ``DEFAULT`` to change.
        reason: Why, in plain words. Required.
        actor: Who changed them, for the audit log.

    Returns:
        The new version number.

    Raises:
        ValueError: Empty reason, no changes, or an invalid key or value.
    """
    if not reason.strip():
        raise ValueError("a reason is required to change thresholds")
    if not changes:
        raise ValueError("no changes given")
    for k, v in changes.items():
        _validate(k, v)
    version, cfg = current()
    new = {**cfg, **changes}
    with db.connect() as con:
        con.execute(
            "INSERT INTO thresholds (version, created_at, reason, config) VALUES (?, ?, ?, ?)",
            [version + 1, naive_utc(), reason, db.dumps(new)],
        )
    audit.log("gauntlet.thresholds", target=f"v{version + 1}", reason=reason, details=changes, actor=actor)
    return version + 1


def history() -> list[dict[str, Any]]:
    """Every threshold version, newest first.

    Returns:
        Rows with ``version, created_at, reason`` and ``config`` decoded.
    """
    rows = db.rows("SELECT * FROM thresholds ORDER BY version DESC")
    for r in rows:
        r["config"] = json.loads(r["config"])
    return rows
