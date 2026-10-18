"""The default research universe: liquid ETFs and the two largest crypto pairs, plus the crypto cross-section.

ETFs avoid the survivorship bias of single stocks in free data. Crypto majors have long, clean histories on
Binance's public archive. The cross-section is every USDT pair the archive holds, delisted ones included; its
manifest (``curated/binance/usdt_universe.json``) records which pairs were kept and why the others weren't.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config

ETFS: dict[str, str] = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq-100",
    "IWM": "Russell 2000",
    "EFA": "Developed markets ex-US",
    "EEM": "Emerging markets",
    "TLT": "US Treasuries 20y+",
    "IEF": "US Treasuries 7-10y",
    "GLD": "Gold",
    "DBC": "Commodities",
    "VNQ": "US real estate",
    "HYG": "US high yield",
    "LQD": "US investment grade",
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health care",
    "XLY": "Consumer discretionary",
    "XLP": "Consumer staples",
    "XLI": "Industrials",
    "XLU": "Utilities",
    "XLB": "Materials",
}

CRYPTO: dict[str, str] = {
    "BTCUSDT": "Bitcoin / Tether",
    "ETHUSDT": "Ether / Tether",
}


def usdt_manifest_path() -> Path:
    """Location of the crypto cross-section manifest.

    Returns:
        ``curated/binance/usdt_universe.json``.
    """
    return config.curated_dir() / "binance" / "usdt_universe.json"


def write_usdt_manifest(manifest: dict[str, Any]) -> None:
    """Store the crypto cross-section manifest.

    Args:
        manifest: Output of ``binance.usdt_universe``.
    """
    path = usdt_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False))


def usdt_manifest() -> dict[str, Any]:
    """The crypto cross-section manifest.

    Returns:
        ``listed_at, n_pairs, kept, excluded, months`` (see ``binance.usdt_universe``).

    Raises:
        FileNotFoundError: The cross-section hasn't been ingested.
    """
    path = usdt_manifest_path()
    if not path.exists():
        raise FileNotFoundError("no crypto cross-section; run `qlab ingest --source binance --universe usdt`")
    return json.loads(path.read_text())
