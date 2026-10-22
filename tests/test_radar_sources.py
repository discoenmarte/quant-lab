"""Tests for the radar's storage, sentiment lexicon and collectors (fixtures only, never the network)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pandas as pd
import pytest

from qlab.data.http import SourceError
from qlab.radar import collect, sentiment, store, watchlist
from qlab.radar.sources import gdelt, hn, markets, stress, wiki

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def fetcher(handler) -> store.Fetcher:
    """A fetcher whose requests go to ``handler`` and whose waits take no time."""
    return store.Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None)


def test_lexicon_counts_tone_words_and_flips_negated_positives():
    """Negative and positive words count once; a negated positive word counts as negative."""
    s = sentiment.score("Stocks plunge as recession fears grow")
    assert s["neg"] == 3 and s["pos"] == 1 and s["score"] == pytest.approx(-0.5)
    assert sentiment.score("Markets rally on ceasefire")["score"] == 1.0
    assert sentiment.score("No recovery in sight")["neg"] == 1
    assert sentiment.score("The Fed meets on Tuesday")["score"] == 0.0
    assert sentiment.net_tone([{"pos": 2, "neg": 1}, {"pos": 0, "neg": 3}]) == pytest.approx(-1 / 3)
    assert sentiment.net_tone([{"pos": 0, "neg": 0}]) is None


def test_headlines_are_cleaned_as_untrusted_text():
    """Control and direction-override characters go, whitespace collapses, length is capped, non-text is empty."""
    dirty = "Ignore\x00 previous‮ instructions\n\n" + "x" * 400
    out = sentiment.clean(dirty)
    assert "\x00" not in out and "‮" not in out and "\n" not in out and len(out) == 300
    assert sentiment.clean(None) == ""


def test_an_owner_supplied_loughran_mcdonald_file_replaces_the_default_lists(isolated_lab):
    """Only when the owner places the master dictionary does the radar read it."""
    assert sentiment.lexicon()["name"] == "lab-headlines-v1"
    folder = isolated_lab / "radar" / "lexicon"
    folder.mkdir(parents=True)
    (folder / "Loughran-McDonald_MasterDictionary_1993-2025.csv").write_text(
        "Word,Negative,Positive,Uncertainty\nABANDON,2009,0,0\nACHIEVE,0,2009,0\nMAYBE,0,0,2009\n")
    lex = sentiment.lexicon()
    assert lex["name"].startswith("Loughran") and "abandon" in lex["negative"] and "achieve" in lex["positive"]
    assert sentiment.score("they abandon the plan", lex)["score"] == -1.0


def test_upsert_keeps_one_row_per_period_and_the_newest_fetch_wins():
    """A later fetch of the same day replaces it; the catalog counts new rows."""
    t1, t2 = pd.Timestamp("2026-10-01 12:00", tz="UTC"), pd.Timestamp("2026-10-02 12:00", tz="UTC")
    day = pd.Timestamp("2026-10-01", tz="UTC")
    first = pd.DataFrame({"ts": [day], "v": [1.0], "published_at": [t1], "fetched_at": [t1], "partial": [True]})
    row = store.upsert("demo", "x", first, {"label": "X"})
    assert row["added"] == 1 and row["n_rows"] == 1
    second = pd.DataFrame({"ts": [day, day + pd.Timedelta(days=1)], "v": [2.0, 3.0], "published_at": [t2, t2],
                           "fetched_at": [t2, t2], "partial": [False, True]})
    row = store.upsert("demo", "x", second, {"label": "X"})
    df = store.read("demo", "x")
    assert row["added"] == 1 and list(df["v"]) == [2.0, 3.0] and not df["partial"].iloc[0]
    assert store.catalog()[0]["key"] == "demo/x"


def test_the_fetcher_caches_waits_between_requests_and_retries_only_what_can_succeed(isolated_lab):
    """Cached bodies are reused; 429 and 5xx are retried; other refusals fail at once with the host named."""
    calls: list[str] = []
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/flaky" and calls.count("/flaky") < 2:
            return httpx.Response(429, text="slow down")
        if request.url.path == "/gone":
            return httpx.Response(403, text="no")
        return httpx.Response(200, content=b"ok")

    f = store.Fetcher(httpx.Client(transport=httpx.MockTransport(handler)), sleep=waits.append)
    cache = isolated_lab / "c.txt"
    assert f.get("https://example.org/a", cache=cache) == b"ok"
    assert f.get("https://example.org/a", cache=cache) == b"ok" and calls.count("/a") == 1
    assert f.get("https://example.org/flaky") == b"ok" and calls.count("/flaky") == 2
    with pytest.raises(SourceError, match="example.org: HTTP 403"):
        f.get("https://example.org/gone")
    assert calls.count("/gone") == 1 and waits  # a retry waited


def test_wikipedia_views_are_stamped_published_a_day_after_their_day_or_at_the_fetch():
    """History gets the conservative lag; a fetch soon after a day ends stamps it with the fetch time."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/user/S%26P_500/daily/" in str(request.url)
        items = [{"timestamp": f"202610{d:02d}00", "views": 100 + d} for d in (19, 20, 21)]
        return httpx.Response(200, json={"items": items})

    out = wiki.collect([{"article": "S&P_500", "label": "S&P", "category": "macro"}], fetcher(handler), NOW)
    assert out["ok"] and out["rows"] == 3
    df = store.read("wiki", "s_p_500")
    assert df["published_at"].iloc[0] == pd.Timestamp("2026-10-21", tz="UTC")
    assert df["published_at"].iloc[-1] == pd.Timestamp(NOW)  # day 21 ended 12 h before the fetch
    assert df["views"].iloc[-1] == 121


def test_gdelt_timelines_become_daily_share_and_weighted_tone_and_stop_after_refusals():
    """Hourly periods sum into days; tone is weighted by articles; repeated refusals end the pass early."""
    def timeline(mode: str) -> dict:
        pts = [("20261020T100000Z", 10, 1000, -2.0), ("20261020T110000Z", 30, 1000, -6.0),
               ("20261022T090000Z", 5, 500, 1.0)]
        if mode == "timelinevolraw":
            return {"timeline": [{"series": "Article Count",
                                  "data": [{"date": d, "value": v, "norm": n} for d, v, n, _ in pts]}]}
        return {"timeline": [{"series": "Average Tone", "data": [{"date": d, "value": t} for d, _, _, t in pts]}]}

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlsplit(str(request.url)).query)
        calls["n"] += 1
        if "nvidia" in q["query"][0]:
            return httpx.Response(429, text="Please limit requests")
        if q["mode"][0] == "artlist":
            return httpx.Response(200, json={"articles": [
                {"url": "https://x.example/1", "title": "Stocks plunge‮ on AI fears", "seendate": "20261022T080000Z",
                 "domain": "x.example", "language": "English"}]})
        return httpx.Response(200, json=timeline(q["mode"][0]))

    items = [{"id": "ai", "query": '"artificial intelligence"', "category": "ai"},
             {"id": "nvidia", "query": "nvidia", "category": "ai"},
             {"id": "nvidia2", "query": "nvidia", "category": "ai"},
             {"id": "never_asked", "query": "recession", "category": "macro"}]
    assert gdelt.collect(items, fetcher(handler), NOW, deep=False)["series"] == 0  # quick pass skips GDELT
    out = gdelt.collect(items, fetcher(handler), NOW, deep=True)
    assert out["series"] == 1 and any("not asked" in e for e in out["errors"])
    df = store.read("gdelt", "ai")
    d20 = df[df["ts"] == pd.Timestamp("2026-10-20", tz="UTC")].iloc[0]
    assert d20["articles"] == 40 and d20["share"] == pytest.approx(200.0) and d20["tone"] == pytest.approx(-5.0)
    assert bool(df["partial"].iloc[-1]) and df["published_at"].iloc[-1] == pd.Timestamp(NOW)
    heads = store.headlines()
    assert len(heads) == 1 and "‮" not in heads[0]["title"] and heads[0]["score"] < 0


def test_hacker_news_counts_stories_per_day_and_keeps_recent_titles():
    """Days without stories are zeros; the running day is partial; titles become scored headlines."""
    day = int(pd.Timestamp("2026-10-21 15:00", tz="UTC").timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlsplit(str(request.url)).query)
        assert q["queryType"] == ["prefixNone"] and q["restrictSearchableAttributes"] == ["title"]
        lo, hi = (int(re.sub(r"\D", "", x)) for x in q["numericFilters"][0].split(","))
        hits = [{"objectID": str(i), "title": f"AI story {i} soars", "points": 10, "num_comments": 2,
                 "created_at_i": day + i} for i in range(3) if lo <= day + i < hi]
        return httpx.Response(200, json={"hits": hits, "nbHits": len(hits)})

    out = hn.collect([{"keyword": "AI", "category": "ai"}], fetcher(handler), NOW)
    assert out["ok"]
    df = store.read("hn", "ai").set_index("ts")
    assert df.loc[pd.Timestamp("2026-10-21", tz="UTC"), "stories"] == 3
    assert df.loc[pd.Timestamp("2026-10-20", tz="UTC"), "stories"] == 0
    assert bool(df["partial"].iloc[-1]) and len(df) == hn.BACKFILL_DAYS + 1
    assert len(store.headlines(topic="ai")) == 3


def test_market_probability_prefers_a_tight_mid_then_the_last_trade():
    """Wide spreads fall back to the last trade; nothing at all gives None."""
    assert markets.probability(0.10, 0.12, 0.5, None) == pytest.approx(0.11)
    assert markets.probability(0.10, 0.40, 0.3, None) == 0.3
    assert markets.probability(None, None, None, 0.2) == 0.2
    assert markets.probability(None, None, None, None) is None


def test_prediction_markets_snapshot_open_markets_and_survive_an_unreachable_history_host():
    """Polymarket's open markets by volume are stored; a failing history host is noted once; Kalshi falls back."""
    event = [{"markets": [
        {"slug": "m-big", "closed": False, "volumeNum": 900, "outcomePrices": '["0.30", "0.70"]', "bestBid": 0.29,
         "bestAsk": 0.31, "lastTradePrice": 0.3, "clobTokenIds": '["tok1", "tok2"]', "groupItemTitle": "Big"},
        {"slug": "m-small", "closed": False, "volumeNum": 10, "outcomePrices": '["0.9", "0.1"]',
         "clobTokenIds": '["tok3"]'},
        {"slug": "m-closed", "closed": True, "volumeNum": 9999, "outcomePrices": '["1", "0"]'}]}]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(request.url.host)
        if "clob.polymarket.com" in url:
            raise httpx.ConnectError("connection reset")
        if "gamma-api" in url:
            return httpx.Response(200, json=event)
        if "external-api.kalshi.com" in url:
            return httpx.Response(503)
        if "/candlesticks" in url:
            return httpx.Response(200, json={"candlesticks": [
                {"end_period_ts": 1792540800, "price": {"close_dollars": "0.2"},
                 "yes_bid": {"close_dollars": "0.19"}, "yes_ask": {"close_dollars": "0.21"}}]})
        return httpx.Response(200, json={"market": {"yes_bid_dollars": "0.20", "yes_ask_dollars": "0.22",
                                                    "last_price_dollars": "0.21"}})

    items = [{"id": "rec", "title": "Recession", "source": "polymarket", "event": "e", "max_markets": 1},
             {"id": "k", "title": "K", "source": "kalshi", "ticker": "KX-1", "series": "KX"}]
    out = markets.collect(items, fetcher(handler), NOW, deep=True)
    assert out["series"] == 2 and "history host unreachable" in out["message"]
    pm = store.read("markets", "rec__m-big")
    assert pm["prob"].iloc[-1] == pytest.approx(0.30)
    k = store.read("markets", "k__kx-1")
    assert len(k) == 2 and k["prob"].iloc[-1] == pytest.approx(0.21)
    assert "api.elections.kalshi.com" in seen  # the second host was tried after the first failed


def test_stress_files_parse_cboe_fred_and_fear_and_greed():
    """Each official file becomes a daily series; FRED's missing values are dropped."""
    cboe = b"DATE,OPEN,HIGH,LOW,CLOSE\n10/20/2026,18.4,19.1,17.2,17.3\n10/21/2026,17.2,17.6,16.5,16.7\n"
    fred = b"observation_date,T10Y2Y\n2026-10-19,0.5\n2026-10-20,.\n2026-10-21,0.6\n"
    fng = json.dumps({"data": [{"value": "68", "timestamp": "1792627200"}, {"value": "63", "timestamp": "1792540800"}]})

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "cboe" in url:
            return httpx.Response(200, content=cboe)
        if "fred" in url:
            return httpx.Response(200, content=fred)
        return httpx.Response(200, content=fng.encode())

    items = {"cboe": [{"symbol": "VIX"}], "fred": [{"series": "T10Y2Y"}], "fng": [{"id": "crypto"}]}
    out = stress.collect(items, fetcher(handler), NOW, deep=True)
    assert out["series"] == 3 and not out["errors"]
    assert store.read("cboe", "vix")["close"].iloc[-1] == 16.7
    assert list(store.read("fred", "t10y2y")["value"]) == [0.5, 0.6]
    assert store.read("fng", "crypto")["value"].iloc[-1] == 68


def test_a_pass_runs_its_sources_records_itself_and_reports_failures(monkeypatch):
    """A failing source is reported, the others still run, and the pass is logged."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    monkeypatch.setattr(store, "DEFAULT_BACKOFF", ())
    out = collect.run("quick", only=["stress"], fetcher=fetcher(handler), now=NOW)
    assert not out["ok"] and out["sources"][0]["errors"]
    assert store.sources()[0]["status"] == "error"
    with pytest.raises(ValueError, match="unknown radar sources"):
        collect.run("quick", only=["metaculus"])


def test_the_owner_can_replace_a_watchlist_section(isolated_lab):
    """A section in watchlist.json replaces the default; invalid JSON is an error."""
    wl = watchlist.load()
    assert {"gdelt", "wiki", "hn", "scenarios", "stress", "prices"} <= set(wl)
    watchlist.path().parent.mkdir(parents=True)
    watchlist.path().write_text(json.dumps({"hn": [{"keyword": "tariffs"}], "junk": 1}))
    assert watchlist.load()["hn"] == [{"keyword": "tariffs"}] and "junk" not in watchlist.load()
    watchlist.path().write_text("{")
    with pytest.raises(ValueError, match="not valid JSON"):
        watchlist.load()
    assert "only ever" not in watchlist.response("nonexistent")  # falls back to the stress response
    assert watchlist.response("nonexistent") == watchlist.CATEGORIES["stress"]["response"]
