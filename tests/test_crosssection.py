"""Tests for M3c: the Binance archive listing, the USDT cross-section filter and its ingest. No network access."""

from datetime import date

import pandas as pd

from qlab import audit
from qlab.data import ingest, universe
from qlab.data.sources import binance

NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def _page(prefixes: list[str], keys: list[str], next_marker: str | None) -> bytes:
    """One page of an S3 bucket listing."""
    truncated = "true" if next_marker else "false"
    marker = f"<NextMarker>{next_marker}</NextMarker>" if next_marker else ""
    body = "".join(f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>" for p in prefixes)
    body += "".join(f"<Contents><Key>{k}</Key><ETag>&quot;abc&quot;</ETag></Contents>" for k in keys)
    return f"<ListBucketResult {NS}><IsTruncated>{truncated}</IsTruncated>{marker}{body}</ListBucketResult>".encode()


def test_bucket_listing_follows_every_page(monkeypatch):
    """The listing asks for the next page after the marker until S3 says it isn't truncated."""
    root = "data/spot/monthly/klines/"
    seen = []

    def fake_get(url, **_):
        seen.append(url)
        if "marker=&" in url or url.endswith("marker="):
            return _page([root + "ANCUSDT/", root + "BTCUSDT/"], [], root + "BTCUSDT/")
        return _page([root + "ZRXUSDT/"], [], None)

    monkeypatch.setattr(binance, "get", fake_get)
    assert binance.list_symbols() == ["ANCUSDT", "BTCUSDT", "ZRXUSDT"]
    assert len(seen) == 2 and "marker=data%2Fspot%2Fmonthly%2Fklines%2FBTCUSDT%2F" in seen[1]


def test_listed_maps_stamps_to_etags_and_ignores_checksums(monkeypatch):
    """Zip keys become month stamps; checksum files are left out."""
    folder = "data/spot/monthly/klines/ANCUSDT/1d/"
    keys = [folder + "ANCUSDT-1d-2022-01.zip", folder + "ANCUSDT-1d-2022-01.zip.CHECKSUM",
            folder + "ANCUSDT-1d-2022-02.zip"]
    monkeypatch.setattr(binance, "get", lambda url, **_: _page([], keys, None))
    binance.listed.cache_clear()
    assert binance.listed("ANCUSDT", "1d") == {"2022-01": "abc", "2022-02": "abc"}


def _fake_archive(monkeypatch, monthly: dict[str, list[str]], daily: dict[str, list[str]]):
    """Serve listings from dicts and record which files get downloaded."""
    got = []
    monkeypatch.setattr(binance, "listed", lambda s, i, kind="monthly", month="": (
        {m: "" for m in monthly.get(s, [])} if kind == "monthly"
        else {d: "" for d in daily.get(s, []) if d.startswith(month)}))

    def download(kind, symbol, interval, stamp, etag=""):
        got.append((kind, stamp))
        day = f"{stamp}-01" if kind == "monthly" else stamp
        ms = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
        return f"{ms},1,2,0.5,1.5,10,{ms + 86399999},15,3,5,7,0\n".encode()

    monkeypatch.setattr(binance, "_download", download)
    return got


def test_fetch_downloads_only_listed_files_and_skips_daily_files_of_delisted_pairs(monkeypatch):
    """A pair delisted in 2022 needs no daily listing; a live pair adds the months without a monthly file."""
    got = _fake_archive(monkeypatch, {"ANCUSDT": ["2022-11", "2022-12"], "BTCUSDT": ["2026-08"]},
                        {"BTCUSDT": ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"]})
    bars = binance.fetch("ANCUSDT", "1d", today=date(2026, 10, 3))
    assert got == [("monthly", "2022-11"), ("monthly", "2022-12")] and len(bars) == 2
    got.clear()
    binance.fetch("BTCUSDT", "1d", today=date(2026, 10, 3))
    assert got == [("monthly", "2026-08"), ("daily", "2026-09-30"), ("daily", "2026-10-01"),
                   ("daily", "2026-10-02")]  # today's file is never complete


def test_parsed_klines_keep_the_quote_volume():
    """Dollar volume (quote volume) survives parsing, for point-in-time universes."""
    bars = binance.parse_klines(b"1733011200000,1,2,0.5,1.5,10,1733097599999,15,3,5,7,0\n")
    assert bars["quote_volume"].tolist() == [15.0]


def test_exclusion_rules_keep_coins_and_drop_pegs_and_leveraged_tokens():
    """Leveraged tokens are recognised by their listed underlying, so JUP and SYRUP stay in."""
    bases = {"BTC", "ETH", "JUP", "SYRUP", "BTCUP", "ETHBEAR", "BULL", "USDC", "PAXG", "WBTC", "NVDAB", "BNB"}
    reason = {b: binance.exclusion_reason(b, bases, "2026-07" if b == "NVDAB" else "2019-01") for b in bases}
    assert reason["BTCUP"] == reason["ETHBEAR"] == reason["BULL"] == "leveraged"
    assert reason["USDC"] == "stablecoin" and reason["PAXG"] == "pegged" and reason["WBTC"] == "wrapped"
    assert reason["NVDAB"] == "tokenized_equity"
    assert reason["JUP"] is reason["SYRUP"] is reason["BTC"] is reason["BNB"] is None


def test_usdt_ingest_writes_the_manifest_and_keeps_delisted_pairs(monkeypatch):
    """The cross-section ingest stores every kept pair, delisted ones included, and records what it left out."""
    monkeypatch.setattr(binance, "list_symbols", lambda: ["ANCUSDT", "BTCUSDT", "BTCUPUSDT", "USDCUSDT", "ETHBTC"])
    _fake_archive(monkeypatch, {"ANCUSDT": ["2022-11"], "BTCUSDT": ["2026-08"], "BTCUPUSDT": ["2021-01"],
                                "USDCUSDT": ["2019-01"]}, {})
    monkeypatch.setattr(binance.listed, "cache_clear", lambda: None, raising=False)
    res = ingest.ingest_usdt_universe()
    m = universe.usdt_manifest()
    assert m["kept"] == ["ANCUSDT", "BTCUSDT"]
    assert m["excluded"] == {"BTCUPUSDT": "leveraged", "USDCUSDT": "stablecoin"}
    assert {e["symbol"] for e in res["series"]} == {"ANCUSDT", "BTCUSDT"}
    assert all(e["interval"] == "1d" for e in res["series"])
    assert any(e["action"] == "data.universe" for e in audit.recent(10))


def test_downloads_are_checked_against_the_listed_md5(monkeypatch):
    """A zip whose bytes don't match the bucket's MD5 is refused; a matching one is cached and reused."""
    import hashlib

    import pytest

    from qlab.data.http import SourceError

    blob = b"PK fake zip"
    calls = []
    monkeypatch.setattr(binance, "get", lambda url, **_: calls.append(url) or blob)
    with pytest.raises(SourceError, match="MD5"):
        binance._download("monthly", "ANCUSDT", "1d", "2022-11", "0" * 32)
    good = hashlib.md5(blob).hexdigest()
    assert binance._download("monthly", "ANCUSDT", "1d", "2022-11", good) == blob
    assert binance._download("monthly", "ANCUSDT", "1d", "2022-11", good) == blob
    assert len(calls) == 2  # the second good call came from the cache
