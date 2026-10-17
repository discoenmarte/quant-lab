"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    """Every test gets its own empty data folder and database."""
    monkeypatch.setenv("QLAB_DATA", str(tmp_path / "data"))
    yield tmp_path / "data"
