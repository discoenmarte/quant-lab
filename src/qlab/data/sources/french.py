"""Kenneth French Data Library: daily Fama-French three factors and the risk-free rate.

The risk-free column (RF, the 1-month T-bill return per day) is the cash rate for backtests. The file is a
zipped CSV with a text preamble, values in percent, and a copyright line at the end.
"""

from __future__ import annotations

import io
import zipfile

import pandas as pd

from ..http import SourceError, get

URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_daily_CSV.zip"
)


def parse_factors(text: str) -> pd.DataFrame:
    """Parse the daily factors CSV.

    Args:
        text: CSV content including the preamble.

    Returns:
        Columns ``ts`` (date at 00:00 UTC), ``mkt_rf, smb, hml, rf`` as fractions (0.01 = 1%).

    Raises:
        SourceError: The header row isn't found.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.replace(" ", "").startswith(",Mkt-RF"))
    except StopIteration as err:
        raise SourceError("Ken French file: header row not found") from err
    rows = []
    for line in lines[start + 1 :]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5 or not (parts[0].isdigit() and len(parts[0]) == 8):
            break
        rows.append(parts[:5])
    df = pd.DataFrame(rows, columns=["date", "mkt_rf", "smb", "hml", "rf"])
    out = pd.DataFrame({"ts": pd.to_datetime(df["date"], format="%Y%m%d", utc=True)})
    for col in ("mkt_rf", "smb", "hml", "rf"):
        out[col] = pd.to_numeric(df[col]) / 100.0
    return out.sort_values("ts").reset_index(drop=True)


def fetch() -> pd.DataFrame:
    """Download and parse the daily factors.

    Returns:
        The parsed factors (see ``parse_factors``).

    Raises:
        SourceError: The download failed or the file format changed.
    """
    blob = get(URL)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        text = zf.read(zf.namelist()[0]).decode("latin-1")
    return parse_factors(text)
