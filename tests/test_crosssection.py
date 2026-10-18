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


# --- Dynamic panels, the drift engine and cross-sectional rules -----------------------------------------------

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from qlab.data import store  # noqa: E402
from qlab.data.bars import normalize_bars  # noqa: E402
from qlab.portfolio import panel as panel_mod  # noqa: E402
from qlab.portfolio.engine import effective_targets, run_portfolio  # noqa: E402
from qlab.portfolio.leakage import future_shift_check  # noqa: E402
from qlab.portfolio.panel import Membership, Panel, membership  # noqa: E402
from qlab.portfolio.strategies import REGISTRY, PortfolioStrategy  # noqa: E402
from qlab.portfolio.synthetic import synthetic_crypto_panel  # noqa: E402

XS = ["xs_weekly_momentum", "xs_reversal", "xs_low_vol", "xs_small_size"]


def tiny_dynamic(close: np.ndarray, member: np.ndarray | None = None, cost: float = 0.0) -> Panel:
    """A dynamic panel with given closes (NaN = no bar), opens at the previous close, and the given membership."""
    t, n = close.shape
    avail = np.isfinite(close)
    open_ = np.vstack([close[:1], close[:-1]])
    open_ = np.where(avail & ~np.isfinite(open_), close, open_)
    member = avail if member is None else member & avail
    return Panel("tiny", pd.date_range("2020-01-01", periods=t, tz="UTC"), [f"C{j}" for j in range(n)],
                 ["binance"] * n, ["crypto"] * n, np.where(avail, open_, np.nan), close, np.zeros(t),
                 np.full(n, cost), avail, member, np.ones((t, n)), Membership(n, 1, 1), drift=True)


def test_membership_is_point_in_time_and_waits_a_full_window():
    """Future volume never changes past membership; a listing enters only after the window; at most top members."""
    rng = np.random.default_rng(1)
    dv = rng.lognormal(10, 1, (60, 8))
    avail = np.ones((60, 8), dtype=bool)
    avail[:20, 7] = False  # listed on day 20
    m = membership(dv, avail, top=3, window=10)
    assert (m.sum(axis=1)[9:] == 3).all() and not m[:9].any()
    assert not m[:29, 7].any()  # needs bars on day 20..29 before it can be a member
    shocked = dv.copy()
    shocked[40:] *= rng.uniform(0.01, 100, shocked[40:].shape)
    assert np.array_equal(membership(shocked, avail, 3, 10)[:40], m[:40])


def test_drift_engine_lets_positions_drift_and_trades_only_on_changes():
    """Between target changes the book drifts with prices and pays nothing; a change pays on the traded amount."""
    close = np.array([[100, 100], [100, 100], [200, 100], [200, 100], [200, 50.0]])
    p = tiny_dynamic(close, cost=0.01)
    w = np.full((5, 2), 0.5)
    res = run_portfolio(p, w)
    assert np.allclose(res.held[1], [0.5, 0.5]) and res.costs[1] == pytest.approx(0.01)
    assert np.allclose(res.held[3], [2 / 3, 1 / 3])  # coin 0 doubled on day 2 and the book wasn't rebalanced
    assert res.costs[2:].sum() == 0
    assert res.net[4] == pytest.approx(1 / 3 * -0.5)
    assert np.allclose(res.net, res.benchmark)  # equal weight of members, weekly, is the same book here


def test_a_delisted_position_exits_at_its_last_close_into_cash():
    """When a coin stops trading, its position is sold at the last close (with costs) and the money waits."""
    close = np.array([[100, 100], [100, 100], [110, 100], [np.nan, 100], [np.nan, 120.0]])
    p = tiny_dynamic(close, cost=0.01)
    res = run_portfolio(p, np.full((5, 2), 0.5))
    assert res.net[2] == pytest.approx(0.5 * 0.10 - 0.01 * 0.5 * 1.1 / 1.05)
    assert res.held[3, 0] == 0 and res.held[3, 1] == pytest.approx(0.5 / 1.05)
    assert res.net[4] == pytest.approx(res.held[4, 1] * 0.2)


def test_targets_respect_membership_on_the_days_they_change():
    """A strategy can't buy a non-member; a holding that leaves the top N is kept until the next change."""
    member = np.ones((6, 2), dtype=bool)
    member[0:2, 1] = False
    member[4:, 0] = False
    w = np.array([[0.5, 0.5]] * 3 + [[0.6, 0.4]] * 3)
    eff = effective_targets(w, member)
    assert np.allclose(eff[:3], [[0.5, 0.0]] * 3)  # coin 1 isn't a member when the weights are decided
    assert np.allclose(eff[3:], [[0.6, 0.4]] * 3)  # coin 0 leaves on day 4 but is held until the next change


def test_equal_weight_is_the_benchmark_and_static_panels_keep_constant_fractions():
    """On a dynamic panel equal weight scores zero edge; a static panel still rebalances to fractions daily."""
    p = synthetic_crypto_panel(3, 60, 20, seed=5)
    res = run_portfolio(p, REGISTRY["equal_weight"].weights(p))
    assert np.allclose(res.net, res.benchmark)
    static = Panel("s", p.ts[:5], ["A", "B"], ["x", "x"], ["crypto", "crypto"],
                   np.array([[100, 100], [100, 100], [200, 100], [200, 100], [200, 100.0]]),
                   np.array([[100, 100], [100, 100], [200, 100], [200, 100], [200, 100.0]]), np.zeros(5), np.zeros(2))
    assert np.allclose(run_portfolio(static, np.full((5, 2), 0.5)).held[3], [0.5, 0.5])


@pytest.mark.parametrize("name", XS)
def test_cross_sectional_rules_hold_only_members_and_are_free_of_look_ahead(name):
    """Every weight sits on a member at its decision date, sums to at most one, and ignores the future."""
    p = synthetic_crypto_panel(4, 80, 30, seed=7)
    w = REGISTRY[name].weights(p)
    eff = effective_targets(w, p.member)
    assert np.allclose(eff, w) and (w.sum(axis=1) <= 1 + 1e-9).all() and (w >= 0).all()
    assert future_shift_check(REGISTRY[name], p)["passed"]


def test_momentum_holds_the_top_fifth_of_members_by_past_return():
    """The bucket is the 20% of members with the highest three-week return."""
    p = synthetic_crypto_panel(3, 80, 30, seed=9)
    w = REGISTRY["xs_weekly_momentum"].weights(p, {"rebalance": 7})
    t = 21 * 7
    past = pd.DataFrame(p.log_returns()).rolling(21, min_periods=21).sum().to_numpy()[t]
    ok = p.member[t] & np.isfinite(past)
    k = round(0.2 * ok.sum())
    best = set(np.argsort(-np.where(ok, past, -np.inf))[:k])
    assert set(np.flatnonzero(w[t] > 0)) == best and np.allclose(w[t][list(best)], 1 / k)


def test_the_dynamic_look_ahead_check_catches_peeking_at_volume_membership_and_delistings():
    """Rules that read tomorrow's membership, volume or availability are flagged."""
    p = synthetic_crypto_panel(4, 80, 30, seed=3)

    def norm(x):
        return x / np.maximum(x.sum(axis=1, keepdims=True), 1)

    cheats = {
        "member": lambda q: norm(np.vstack([q.member[1:], q.member[-1:]]).astype(float)),
        "volume": lambda q: norm((lambda dv: dv >= np.sort(dv, axis=1)[:, -5][:, None])(
            np.nan_to_num(np.vstack([q.dollar_volume[1:], q.dollar_volume[-1:]]))).astype(float)),
        "delisting": lambda q: norm((np.vstack([q.avail[1:], q.avail[-1:]]) & q.member).astype(float)),
    }
    for name, fn in cheats.items():
        assert not future_shift_check(PortfolioStrategy(name, name, "", fn, kind="dynamic"), p)["passed"], name


def test_both_masked_shuffles_keep_the_calendar_and_each_runs_first_bar():
    """Null worlds keep who trades when; the cross-sectional one keeps each day's set of unit moves (simple
    returns over local volatility) within the members and within the other pairs."""
    from qlab.gauntlet.subjects import _unit_moves, cross_sectional_permuter, masked_permuter

    p = synthetic_crypto_panel(3, 60, 20, seed=4)
    rng = np.random.default_rng(0)
    for maker in (masked_permuter, cross_sectional_permuter):
        q = maker(p)(rng)
        assert np.array_equal(np.isfinite(q.close), p.avail) and np.array_equal(q.member, p.member)
        first = p.avail & ~np.vstack([np.zeros((1, p.n), dtype=bool), p.avail[:-1]])
        assert np.allclose(q.close[first], p.close[first])
    valid, vol, ug, ub, *_ = _unit_moves(p)
    q = cross_sectional_permuter(p)(rng)
    _, _, ug_q, ub_q, *_ = _unit_moves(q)  # same volatility path, so the units match
    t = int(np.argmax(valid.sum(axis=1)))
    moved = (q.close[t] / q.close[t - 1] - 1.0) / vol[t]  # opens equal prior closes, so the body is the move
    for group in (p.member[t - 1], ~p.member[t - 1]):  # members trade moves among themselves, the rest likewise
        cells = valid[t] & group
        assert np.allclose(np.sort((ug[t] + ub[t])[cells]), np.sort(moved[cells]))


def _write_coin(symbol: str, start: str, n: int, seed: int, volume: float) -> None:
    """Store daily bars for a fake Binance pair."""
    rng = np.random.default_rng(seed)
    close = 10 * np.exp(np.cumsum(rng.normal(0, 0.03, n)))
    ts = pd.date_range(start, periods=n, tz="UTC")
    store.write_series("binance", symbol, "1d", normalize_bars(pd.DataFrame({
        "ts": ts, "open": np.concatenate([[close[0]], close[:-1]]), "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": volume / close, "quote_volume": np.full(n, volume)})))


def test_a_dynamic_universe_loads_from_the_manifest_with_delisted_pairs_and_split_tickers(monkeypatch):
    """Union calendar, NaN where a pair doesn't trade, a reused ticker split at its jump, and crypto_xs costs."""
    universe.write_usdt_manifest({"kept": ["AUSDT", "BUSDT", "CUSDT", "DUSDT"], "excluded": {}})
    _write_coin("AUSDT", "2020-01-01", 300, 1, 5e6)
    _write_coin("BUSDT", "2020-01-01", 120, 2, 4e6)  # delisted after 120 days
    _write_coin("CUSDT", "2020-03-01", 200, 3, 3e6)
    _write_coin("DUSDT", "2020-01-01", 10, 4, 1e9)  # listed for ten days: never a full 30-day window
    bars = store.read_series("binance", "AUSDT", "1d")
    bars.loc[200:, ["open", "high", "low", "close"]] *= 1000  # a ticker reused for a new coin
    store.write_series("binance", "AUSDT", "1d", bars)
    monkeypatch.setitem(panel_mod.UNIVERSES, "tiny_xs", {"members": "binance_usdt", "membership": Membership(2, 30, 2),
                                                        "costs": "crypto_xs", "description": "test"})
    p = panel_mod.load_panel("tiny_xs")
    assert p.dynamic and p.drift and p.symbols == ["AUSDT", "BUSDT", "CUSDT"]
    assert p.ts[0] == pd.Timestamp("2020-01-30", tz="UTC")  # 30 days of history for two members
    b = p.symbols.index("BUSDT")
    assert not p.avail[p.ts >= pd.Timestamp("2020-04-30", tz="UTC"), b].any()
    assert np.isnan(p.close[~p.avail]).all()
    a = p.symbols.index("AUSDT")
    jump = int(np.flatnonzero(p.ts == pd.Timestamp("2020-07-19", tz="UTC"))[0])
    assert not p.avail[jump, a] and not p.member[jump:jump + 29, a].any()
    assert p.costs[0] == pytest.approx(0.002)


def test_the_runner_refuses_rules_built_for_the_other_kind_of_universe():
    """A static trend rule on a dynamic cross-section is an error, not a quiet run in cash."""
    from qlab.portfolio.runner import check_fit

    p = synthetic_crypto_panel(2, 40, 20, seed=1)
    with pytest.raises(ValueError, match="built for static"):
        check_fit(REGISTRY["trend_filter"], p)
    check_fit(REGISTRY["xs_weekly_momentum"], p)
    check_fit(REGISTRY["equal_weight"], p)


def test_the_gauntlet_judges_a_dynamic_panel_noise_fails_and_planted_momentum_passes():
    """The ten stages run on a synthetic cross-section with listings and delistings."""
    from qlab.gauntlet import pipeline, thresholds
    from qlab.gauntlet.calibration import xs_panel
    from qlab.gauntlet.subjects import PanelSubject

    thresholds.update({"n_perm": 60, "n_boot": 60, "grid_max": 30}, "fast tests")
    _, th = thresholds.current()
    for planted, admitted in ((0.0, False), (2.5, True)):
        p = xs_panel("xs30/xs_weekly_momentum", 6.5, planted, 41)
        sub = PanelSubject(p, "xs_weekly_momentum", p.ts[-1] + pd.Timedelta(days=1), "synthetic_crypto30")
        out = pipeline.evaluate(sub, sub.strat.defaults(), th=th)
        assert (out["failed_stage"] is None) == admitted, out["failed_stage"]
