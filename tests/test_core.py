"""Tests for the M0 core: ledger, audit log, metrics and the Lab API."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qlab import audit, ledger
from qlab.api.main import create_app
from qlab.backtest import metrics


def test_ledger_counts_trials_per_question():
    """N is counted per research question, and undefined Sharpe ratios are stored as NULL."""
    for _ in range(3):
        ledger.record_trial(
            question="q1", family="f", strategy="s", params={"a": 1}, symbol="X",
            start="2020-01-01", end="2021-01-01", source="test", metrics={"sharpe": 0.5, "n_obs": 252},
        )
    ledger.record_trial(
        question="q2", family="f", strategy="s", params={}, symbol="X",
        start=None, end=None, source="test", metrics={"sharpe": float("nan")},
    )
    assert ledger.count_trials("q1") == 3
    assert ledger.count_trials() == 4
    assert ledger.sharpes("q1") == [0.5, 0.5, 0.5]
    assert ledger.sharpes("q2") == []  # NaN Sharpe is stored as NULL, not as a number


def test_audit_log_roundtrip():
    """An event written to the audit log comes back first, with its reason."""
    audit.log("test.action", target="t1", reason="because", details={"k": 1})
    events = audit.recent()
    assert events[0]["action"] == "test.action"
    assert events[0]["reason"] == "because"


def test_metrics_basic():
    """Drawdown, total return and the zero-volatility Sharpe edge case."""
    r = pd.Series([0.01, -0.02, 0.03, 0.0], index=pd.bdate_range("2024-01-01", periods=4))
    assert metrics.max_drawdown(r) == pytest.approx(0.02)
    assert metrics.summary(r)["total_return"] == pytest.approx(1.01 * 0.98 * 1.03 - 1)
    assert np.isnan(metrics.sharpe(pd.Series([0.01, 0.01, 0.01])))


def test_api_health_system_and_demo():
    """Each demo run adds one trial to the same question, and the API reports it."""
    client = TestClient(create_app())
    assert client.get("/api/health").json()["ok"] is True
    first = client.post("/api/demo").json()
    second = client.post("/api/demo").json()
    assert second["n_trials"] == first["n_trials"] + 1
    system = client.get("/api/system").json()
    assert system["counts"]["trials"] == 2
    assert client.get("/api/audit").json()[0]["action"] == "demo.run"


def test_backups_are_consistent_copies_and_old_ones_are_dropped():
    """A snapshot opens as a database with the same rows; only the newest ``keep`` stay."""
    import time

    import duckdb

    from qlab import ops

    audit.log("test.event", target="x")
    first = ops.backup(keep=2)
    time.sleep(1.1)
    ops.backup(keep=2)
    time.sleep(1.1)
    third = ops.backup(keep=2)
    con = duckdb.connect(third["path"], read_only=True)
    assert con.execute("SELECT count(*) FROM audit WHERE action = 'test.event'").fetchone()[0] == 1
    con.close()
    assert first["path"].split("/")[-1] in third["removed"]
    m = ops.machine()
    assert len(m["snapshots"]) == 2 and m["disk_free_gb"] > 0


def test_the_heartbeat_sees_a_silent_api_and_a_missing_report():
    """Health problems come from the API and the latest report, judged against the last US close."""
    from datetime import UTC, datetime

    import httpx

    from qlab import ops

    class Res:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Client:
        def __init__(self, report):
            self.report = report

        def get(self, url, timeout):
            if url.endswith("/health"):
                return Res({"ok": True})
            return Res(self.report)

    class Down:
        def get(self, url, timeout):
            raise httpx.ConnectError("refused")

    late = datetime(2026, 10, 23, 7, tzinfo=UTC)  # 03:00 New York, eleven hours after the close
    assert ops.health_problems("x", late, Down()) == ["the Lab API doesn't answer"]
    assert ops.health_problems("x", late, Client({"created_at": "2026-10-22T23:00:00"})) == []
    assert "since the 2026-10-22" in ops.health_problems("x", late, Client({"created_at": "2026-10-22T10:00:00"}))[0]
    assert ops.health_problems("x", datetime(2026, 10, 22, 21, tzinfo=UTC), Client(None)) == []  # still in grace
