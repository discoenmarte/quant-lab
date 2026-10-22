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


def env_files() -> list[Path]:
    """Files of ``NAME=value`` settings, in the order they are searched.

    Returns:
        ``<data>/lab.env``, then ``<repo>/.env``. Both are ignored by git.
    """
    return [env_file(), ROOT / ".env"]


def setting(name: str, default: str | None = None) -> str | None:
    """A setting from the environment, or else from ``lab.env``, or else from the repository's ``.env``.

    Args:
        name: Variable name, e.g. ``QLAB_TELEGRAM_TOKEN``.
        default: Value when no source has it.

    Returns:
        The value with surrounding whitespace and quotes removed, or ``default``.
    """
    if name in os.environ:
        return os.environ[name].strip()
    for path in env_files():
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.strip().removeprefix("export ").strip() == name:
                return value.strip().strip("'\"")
    return default


def save_setting(name: str, value: str) -> Path:
    """Write ``NAME=value`` into ``lab.env``, replacing an earlier line for the same name.

    The file is created readable by its owner only, since it holds secrets.

    Args:
        name: Variable name.
        value: Value (no newlines).

    Returns:
        The file written.

    Raises:
        ValueError: The name or value holds a newline or the name holds ``=``.
    """
    if "\n" in name or "\n" in value or "=" in name or not name.strip():
        raise ValueError("a setting is one NAME=value line")
    path = env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [ln for ln in lines if ln.strip().removeprefix("export ").partition("=")[0].strip() != name]
    path.write_text("\n".join([*kept, f"{name}={value}"]) + "\n")
    path.chmod(0o600)
    return path


def ensure_dirs() -> None:
    """Create the data, raw, curated and reports folders if they're missing."""
    for d in (data_dir(), raw_dir(), curated_dir(), reports_dir()):
        d.mkdir(parents=True, exist_ok=True)
