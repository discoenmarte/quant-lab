"""Paths and settings.

Everything lives under the repo's ``data/`` folder unless ``QLAB_DATA`` is set. Paths are functions (not
constants) so tests can point the lab at a temporary folder with an environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("QLAB_HOME", Path(__file__).resolve().parents[2]))
EXCHANGE_TZ = "America/New_York"


def data_dir() -> Path:
    return Path(os.environ.get("QLAB_DATA", ROOT / "data"))


def raw_dir() -> Path:
    return data_dir() / "raw"


def curated_dir() -> Path:
    return data_dir() / "curated"


def reports_dir() -> Path:
    return data_dir() / "reports"


def db_path() -> Path:
    return data_dir() / "lab.duckdb"


def web_dist() -> Path:
    return ROOT / "web" / "dist"


def ensure_dirs() -> None:
    for d in (data_dir(), raw_dir(), curated_dir(), reports_dir()):
        d.mkdir(parents=True, exist_ok=True)
