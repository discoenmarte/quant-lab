"""The default research universe: liquid ETFs and the two largest crypto pairs.

ETFs avoid the survivorship bias of single stocks in free data. Crypto majors have long, clean histories on
Binance's public archive.
"""

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
