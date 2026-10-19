"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    """Every test gets its own empty data folder and database, and alerts go to the file channel only."""
    monkeypatch.setenv("QLAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("QLAB_ALERT_CHANNELS", "file")
    for name in ("QLAB_TELEGRAM_TOKEN", "QLAB_TELEGRAM_CHAT"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path / "data"
