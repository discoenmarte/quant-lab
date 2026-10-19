"""Paths and settings for the lab.

Everything lives under the repo's ``data/`` folder unless ``QLAB_DATA`` points elsewhere. Paths are
functions rather than constants so tests can redirect the whole lab with one environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("QLAB_HOME", Path(__file__).resolve().parents[2]))
EXCHANGE_TZ = "America/New_York"


def data_dir() -> Path:
    """Root folder for all lab data.

    Returns:
        ``$QLAB_DATA`` if set, otherwise ``<repo>/data``.
    """
    return Path(os.environ.get("QLAB_DATA", ROOT / "data"))


def raw_dir() -> Path:
    """Folder for files exactly as downloaded from a source.

    Returns:
        ``<data>/raw``.
    """
    return data_dir() / "raw"


def curated_dir() -> Path:
    """Folder for cleaned Parquet series, one file per source, symbol and interval.

    Returns:
        ``<data>/curated``.
    """
    return data_dir() / "curated"


def reports_dir() -> Path:
    """Folder for JSON reports produced by backtests, the gauntlet and calibration.

    Returns:
        ``<data>/reports``.
    """
    return data_dir() / "reports"


def db_path() -> Path:
    """Location of the DuckDB file holding the ledger, audit log and catalog.

    Returns:
        ``<data>/lab.duckdb``.
    """
    return data_dir() / "lab.duckdb"


def web_dist() -> Path:
    """Folder of the built web app that the API serves at ``/``.

    Returns:
        ``<repo>/web/dist``.
    """
    return ROOT / "web" / "dist"


def env_file() -> Path:
    """Optional file of ``NAME=value`` settings, for secrets such as an alert token.

    It lives in the data folder, which git ignores, so a token written there can't be committed. Background jobs
    (launchd, the API server) read it too, without the token appearing in a plist or a shell profile.

    Returns:
        ``<data>/lab.env``.
    """
    return data_dir() / "lab.env"


def setting(name: str, default: str | None = None) -> str | None:
    """A setting from the environment, or else from ``lab.env``.

    Args:
        name: Variable name, e.g. ``QLAB_TELEGRAM_TOKEN``.
        default: Value when neither source has it.

    Returns:
        The value with surrounding whitespace and quotes removed, or ``default``.
    """
    if name in os.environ:
        return os.environ[name].strip()
    path = env_file()
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.strip().removeprefix("export ").strip() == name:
                return value.strip().strip("'\"")
    return default


def ensure_dirs() -> None:
    """Create the data, raw, curated and reports folders if they're missing."""
    for d in (data_dir(), raw_dir(), curated_dir(), reports_dir()):
        d.mkdir(parents=True, exist_ok=True)
