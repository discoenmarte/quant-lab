"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    """Every test gets its own empty data folder and database, alerts go to the file channel only, and no test sees
    the developer's secrets.

    Settings come only from the environment and the test's own ``lab.env``: the repository's ``.env`` may hold real
    paper-broker keys or an alert token, and a test must never reach those accounts.
    """
    from qlab import config

    monkeypatch.setenv("QLAB_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("QLAB_ALERT_CHANNELS", "file")
    monkeypatch.setattr(config, "env_files", lambda: [config.env_file()])
    for name in ("QLAB_TELEGRAM_TOKEN", "QLAB_TELEGRAM_CHAT", "ALPACA_KEY", "ALPACA_SECRET", "ALPACA_ENDPOINT",
                 "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "QLAB_ALPACA_KEY", "QLAB_ALPACA_SECRET",
                 "QLAB_BINANCE_TESTNET_KEY", "QLAB_BINANCE_TESTNET_SECRET"):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path / "data"
