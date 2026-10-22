"""What the radar watches, and the response committed in advance for each kind of anomaly.

The defaults below were checked against each source on 2026-10-22: every Wikipedia title has daily views since
2015-07-01, every GDELT query parses, and every prediction market was open. A JSON file at
``<data>/radar/watchlist.json`` with any of the top-level keys replaces that section (markets close and new ones
open; the owner edits the file, not the code).

Responses are written before any anomaly is seen, so an alert never invites improvisation. They only ever reduce risk
or ask for reading: none starts a trade, enrols a strategy or loosens a limit.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .. import config

CATEGORIES: dict[str, dict[str, str]] = {
    "ai": {"label": "AI",
           "response": "Read what is behind it before acting on anything. Attention to AI news predicts volatility "
                       "and trading volume better than direction, so nothing is bought or sold because of it."},
    "macro": {"label": "Macro",
              "response": "Read what is behind it. No new strategy enters the paper book while it lasts; existing "
                          "entries keep their pre-registered rules."},
    "geopolitics": {"label": "Geopolitics",
                    "response": "Read what is behind it. No new strategy enters the paper book while it lasts; "
                                "existing entries keep their pre-registered rules."},
    "crypto": {"label": "Crypto",
               "response": "Check the crypto sleeves are inside their limits. Nothing is added to crypto while it "
                           "lasts."},
    "stress": {"label": "Market stress",
               "response": "No new positions or enrolments until it clears; check each paper sleeve is inside its "
                           "limits. If several severe signals coincide, new paper orders pause (automatically only "
                           "with QLAB_RADAR_AUTOPAUSE on). Positions are kept: selling into a panic is a decision "
                           "for the owner, with a reason."},
    "scenario": {"label": "Scenarios",
                 "response": "Note it on the scenario board. A crowd forecast informs; it is never a trading signal "
                             "unless a pre-registered gauntlet test says so."},
}

# GDELT DOC 2.0 queries. English-language sources only, so coverage stays comparable across years; OR-ed terms must
# sit in parentheses (GDELT's syntax).
GDELT = [
    {"id": "ai", "query": '"artificial intelligence"', "label": "News on artificial intelligence", "category": "ai"},
    {"id": "nvidia", "query": "nvidia", "label": "News on Nvidia", "category": "ai"},
    {"id": "openai", "query": "openai", "label": "News on OpenAI", "category": "ai"},
    {"id": "chip_export", "query": '"export controls" (chip OR chips OR semiconductor OR semiconductors)',
     "label": "News on chip export controls", "category": "ai"},
    {"id": "recession", "query": "recession", "label": "News on recession", "category": "macro"},
    {"id": "fed", "query": '"federal reserve"', "label": "News on the Federal Reserve", "category": "macro"},
    {"id": "tariffs", "query": "tariffs", "label": "News on tariffs", "category": "macro"},
    {"id": "bank_failure", "query": '("bank failure" OR "bank run" OR "bank collapse")',
     "label": "News on bank failures", "category": "macro"},
    {"id": "markets", "query": '("stock market" OR "wall street")', "label": "News on the stock market",
     "category": "macro"},
    {"id": "war", "query": "(war OR invasion OR airstrike)", "label": "News on war", "category": "geopolitics"},
    {"id": "crypto_crash", "query": "(bitcoin OR crypto) (crash OR plunge OR selloff)",
     "label": "News on a crypto crash", "category": "crypto"},
]

# English Wikipedia articles: daily views by people (bots and automated agents excluded), since 2015-07-01.
WIKI = [
    {"article": "S&P_500", "label": "Wikipedia: S&P 500", "category": "macro"},
    {"article": "Stock_market_crash", "label": "Wikipedia: Stock market crash", "category": "macro"},
    {"article": "Recession", "label": "Wikipedia: Recession", "category": "macro"},
    {"article": "Inflation", "label": "Wikipedia: Inflation", "category": "macro"},
    {"article": "Federal_Reserve", "label": "Wikipedia: Federal Reserve", "category": "macro"},
    {"article": "Bank_run", "label": "Wikipedia: Bank run", "category": "macro"},
    {"article": "Artificial_intelligence", "label": "Wikipedia: Artificial intelligence", "category": "ai"},
    {"article": "Artificial_general_intelligence", "label": "Wikipedia: AGI", "category": "ai"},
    {"article": "Nvidia", "label": "Wikipedia: Nvidia", "category": "ai"},
    {"article": "OpenAI", "label": "Wikipedia: OpenAI", "category": "ai"},
    {"article": "Bitcoin", "label": "Wikipedia: Bitcoin", "category": "crypto"},
    {"article": "Cryptocurrency", "label": "Wikipedia: Cryptocurrency", "category": "crypto"},
    {"article": "World_War_III", "label": "Wikipedia: World War III", "category": "geopolitics"},
    {"article": "Nuclear_warfare", "label": "Wikipedia: Nuclear warfare", "category": "geopolitics"},
    {"article": "Tariff", "label": "Wikipedia: Tariff", "category": "macro"},
]

# Hacker News stories per keyword (Algolia's search; titles, links and text).
HN = [
    {"keyword": "AI", "label": "HN: AI", "category": "ai"},
    {"keyword": "OpenAI", "label": "HN: OpenAI", "category": "ai"},
    {"keyword": "AGI", "label": "HN: AGI", "category": "ai"},
    {"keyword": "Nvidia", "label": "HN: Nvidia", "category": "ai"},
    {"keyword": "layoffs", "label": "HN: layoffs", "category": "macro"},
    {"keyword": "recession", "label": "HN: recession", "category": "macro"},
    {"keyword": "bitcoin", "label": "HN: bitcoin", "category": "crypto"},
]

# Prediction markets as crowd forecasts. Polymarket by event slug (each open market in it is followed, up to
# ``max_markets`` by volume); Kalshi by market ticker. Base rates are stated only where history gives one.
SCENARIOS = [
    {"id": "us_recession_2026", "title": "US recession by end of 2026", "category": "macro", "source": "polymarket",
     "event": "us-recession-by-end-of-2026",
     "base_rate": "13 US recessions began in the 81 years 1945-2025 (NBER peaks): about 16% a year."},
    {"id": "us_recession_2027", "title": "US recession by end of 2027", "category": "macro", "source": "polymarket",
     "event": "us-recession-by-end-of-2027-20260807185409760",
     "base_rate": "About 30% over two years at the 1945-2025 rate of 16% a year (NBER peaks)."},
    {"id": "kalshi_recession_2027", "title": "Recession in 2027 (Kalshi)", "category": "macro", "source": "kalshi",
     "ticker": "KXRECSSNBER-27", "series": "KXRECSSNBER",
     "base_rate": "13 US recessions began in the 81 years 1945-2025 (NBER peaks): about 16% a year."},
    {"id": "fed_december", "title": "Fed decision in December 2026", "category": "macro", "source": "polymarket",
     "event": "fed-decision-in-december-20260729232808632", "max_markets": 4},
    {"id": "fed_cuts_2026", "title": "Fed rate cuts in 2026", "category": "macro", "source": "polymarket",
     "event": "how-many-fed-rate-cuts-in-2026", "max_markets": 3},
    {"id": "openai_agi_2026", "title": "OpenAI announces AGI before 2027", "category": "ai", "source": "polymarket",
     "event": "openai-announces-it-has-achieved-agi-before-2027"},
    {"id": "kalshi_openai_agi_2027", "title": "OpenAI announces AGI before 2028 (Kalshi)", "category": "ai",
     "source": "kalshi", "ticker": "OAIAGI-27", "series": "KXOAIAGI"},
    {"id": "kalshi_agi_any_2028", "title": "Any company announces AGI before 2029 (Kalshi)", "category": "ai",
     "source": "kalshi", "ticker": "KXAGICO-COMP-28Q4", "series": "KXAGICO"},
    {"id": "best_ai_model_2026", "title": "Best AI model at the end of 2026", "category": "ai",
     "source": "polymarket", "event": "which-company-has-best-ai-model-end-of-2026", "max_markets": 3},
    {"id": "bitcoin_2026", "title": "Bitcoin price levels hit in 2026", "category": "crypto", "source": "polymarket",
     "event": "what-price-will-bitcoin-hit-before-2027", "max_markets": 6},
    {"id": "taiwan_clash_2026", "title": "China x Taiwan military clash before 2027", "category": "geopolitics",
     "source": "polymarket", "event": "china-x-taiwan-military-clash-before-2027"},
    {"id": "taiwan_invasion_2027", "title": "China invades Taiwan by June 30, 2027", "category": "geopolitics",
     "source": "polymarket", "event": "will-china-invade-taiwan-by-june-30-2027"},
    {"id": "ukraine_ceasefire", "title": "Russia x Ukraine ceasefire agreement", "category": "geopolitics",
     "source": "polymarket", "event": "russia-x-ukraine-ceasefire-agreement-by", "max_markets": 3},
]

# Market stress from free official files: CBOE's VIX history, FRED, and the crypto Fear & Greed index.
STRESS = {
    "cboe": [{"symbol": "VIX", "label": "VIX (30-day implied volatility)"},
             {"symbol": "VIX3M", "label": "VIX3M (3-month implied volatility)"}],
    "fred": [{"series": "BAMLH0A0HYM2", "label": "US high-yield spread (ICE BofA OAS)", "unit": "%"},
             {"series": "T10Y2Y", "label": "10-year minus 2-year Treasury yield", "unit": "%"}],
    "fng": [{"id": "crypto", "label": "Crypto Fear & Greed index"}],
}

# The lab's own daily prices for returns, realised volatility and cross-asset correlation.
PRICES = {"yahoo": ["SPY", "QQQ", "TLT", "GLD", "HYG"], "binance": ["BTCUSDT", "ETHUSDT"]}

DEFAULTS: dict[str, Any] = {"gdelt": GDELT, "wiki": WIKI, "hn": HN, "scenarios": SCENARIOS, "stress": STRESS,
                            "prices": PRICES}


def path():
    """Where the owner's watchlist overrides live.

    Returns:
        ``<data>/radar/watchlist.json``.
    """
    return config.data_dir() / "radar" / "watchlist.json"


def load() -> dict[str, Any]:
    """The watchlist in use: the defaults with any section the owner's file replaces.

    Returns:
        Sections ``gdelt``, ``wiki``, ``hn``, ``scenarios``, ``stress`` and ``prices``.

    Raises:
        ValueError: The owner's file isn't valid JSON or isn't an object.
    """
    out = copy.deepcopy(DEFAULTS)
    p = path()
    if p.exists():
        try:
            own = json.loads(p.read_text())
        except json.JSONDecodeError as err:
            raise ValueError(f"{p} is not valid JSON: {err}") from None
        if not isinstance(own, dict):
            raise ValueError(f"{p} must hold a JSON object")
        out.update({k: v for k, v in own.items() if k in DEFAULTS})
    return out


def response(category: str) -> str:
    """The pre-committed response for a category.

    Args:
        category: A key of ``CATEGORIES``.

    Returns:
        The response text; the stress response for an unknown category (the most cautious).
    """
    return CATEGORIES.get(category, CATEGORIES["stress"])["response"]
