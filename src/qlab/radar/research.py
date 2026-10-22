"""The radar's pre-registered research: attention and news tone as de-risk rules, point in time.

The hypotheses (``docs/preregistration/2026-10-22-m10-radar-attention.md``, decision 0016) say attention spikes and
drops in news tone come before higher volatility, so holding less after them improves risk-adjusted returns. Two
kinds of test:

- **Trading rules the gauntlet judges** (registered in ``backtest.strategies``): hold the index, and step out for
  ``hold`` days after a spike, a robust z-score of the signal beyond ``threshold`` against its trailing ``window``
  days. ``attention_derisk`` reads market attention on Wikipedia (S&P 500, Stock market crash, Recession),
  ``crypto_attention_derisk`` crypto attention (Bitcoin, Cryptocurrency), ``tone_derisk`` GDELT's average tone of
  English news on the stock market.
- **A forecasting test** (``har_test``): does the signal add to a HAR model of next-day volatility (Corsi, 2009)?
  It is a regression, not a backtest, so it adds no trial; it is pre-registered all the same.

Point in time:

- **Decision time.** A daily bar's position is decided at 20:00 UTC of its day: at or before every close the lab
  trades (16:00 New York is 20:00 or 21:00 UTC; Binance's day ends at 24:00 UTC). The engine applies it from the next
  bar's open.
- **Publication.** A signal day counts only once its row's ``published_at`` is at or before the decision. Wikipedia
  history is stamped 24 hours after the day ends, so a bar sees views through two days before it; GDELT is stamped an
  hour after the day ends, so a bar sees the previous day's tone. Each decision looks at every day published since
  the previous one (Monday's learns about Thursday to Saturday) and takes the most extreme score.
- **Revisions.** GDELT recomputes old days from its current index, and Wikipedia's bot filter changed in 2020. The
  values are today's view of the past; the radar keeps ``fetched_at`` so later vintages can be compared.

Calibration (``synthetic_signal_bars``): the gauntlet's thresholds were set on trend-following markets, so these rules
are calibrated first on synthetic markets with a signal column. In noise markets the signal is independent of
everything, so every rule has a true edge of exactly zero; in planted markets a signal spike is followed by five days
of higher volatility, which a de-risk rule can avoid.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from ..data.bars import normalize_bars
from . import detect, store

ATTENTION = {"attention_derisk": ("S&P_500", "Stock_market_crash", "Recession"),
             "crypto_attention_derisk": ("Bitcoin", "Cryptocurrency")}
TONE_QUERY = "markets"  # the GDELT watchlist id of English news on the stock market
DECISION = pd.Timedelta(hours=20)
WIKI_STARTS = "2015-11-01"  # views begin 2015-07-01; the longest baseline in the grid is 120 days
TONE_STARTS = "2017-06-01"  # GDELT's DOC API begins 2017-01-01
SPIKE_RATE = 1 / 30  # synthetic spikes per day
BURST_DAYS = 5  # days of higher volatility after a planted spike
BURST_SCALE = 9.0  # planted size 1 multiplies volatility by 10 during a burst


def _wiki_name(article: str) -> str:
    """The radar's series name of an English Wikipedia article.

    Args:
        article: Title with underscores.

    Returns:
        The slug used by ``sources.wiki``.
    """
    return store.slug(article)


@lru_cache(maxsize=8)
def _load(source: str, name: str, mtime: int) -> pd.DataFrame:
    """A stored radar series, cached until its file changes.

    Args:
        source: Source name.
        name: Series name.
        mtime: The file's modification time (the cache key).

    Returns:
        The rows.
    """
    return store.read(source, name)


def series(source: str, name: str) -> pd.DataFrame:
    """A stored radar series for research.

    Args:
        source: ``wiki`` or ``gdelt``.
        name: Series name.

    Returns:
        Rows with ``ts`` and ``published_at``.

    Raises:
        FileNotFoundError: Not collected (``qlab radar collect --deep``).
    """
    path = store.series_path(source, name)
    if not path.exists():
        raise FileNotFoundError(f"radar series {source}/{name} not collected: run `qlab radar collect --deep`")
    return _load(source, name, path.stat().st_mtime_ns)


def latest_known(df: pd.DataFrame, decisions: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The newest day of a series published by each decision time.

    Args:
        df: A radar series (``ts`` and ``published_at``).
        decisions: Decision times (UTC).

    Returns:
        One day per decision; NaT before the first publication.
    """
    pub = df[["ts", "published_at"]].sort_values("published_at").copy()
    pub["latest"] = pub["ts"].cummax()  # the newest day known once each row is out
    left = pd.DataFrame({"decision": pd.DatetimeIndex(decisions).as_unit("ns")})
    right = pub[["published_at", "latest"]].rename(columns={"published_at": "decision"})
    right["decision"] = right["decision"].dt.as_unit("ns")
    right["latest"] = right["latest"].dt.as_unit("ns")
    return pd.DatetimeIndex(pd.merge_asof(left, right, on="decision", direction="backward")["latest"])


def newly_known(df: pd.DataFrame, z: pd.Series, decisions: pd.DatetimeIndex, side: str) -> np.ndarray:
    """At each decision, the most extreme score among the days published since the previous decision.

    A daily signal publishes on weekends too, while an ETF decides on weekdays only: Monday's decision learns about
    Thursday to Saturday at once, so it looks at all of them.

    Args:
        df: The radar series (``ts`` and ``published_at``).
        z: Scores by the series' days (sorted).
        decisions: Decision times, ascending.
        side: ``up`` takes the largest score, ``down`` the smallest.

    Returns:
        One score per decision; NaN when nothing new was published since the previous decision.
    """
    days = pd.DatetimeIndex(z.index).as_unit("ns")
    values = z.to_numpy(dtype=float)
    latest = latest_known(df, decisions)
    pos = np.where(latest.isna(), 0, days.searchsorted(latest.fillna(days[0]), side="right"))
    out = np.full(len(decisions), np.nan)
    prev: int | None = None
    pick = np.nanmax if side == "up" else np.nanmin
    for i, p in enumerate(pos):
        if p == 0:
            continue
        lo = p - 1 if prev is None else prev
        if p > lo:
            seg = values[lo:p]
            if np.isfinite(seg).any():
                out[i] = pick(seg)
        prev = p
    return out


def _decisions(bars: pd.DataFrame) -> pd.DatetimeIndex:
    """Decision time of each daily bar.

    Args:
        bars: Normalised daily bars.

    Returns:
        20:00 UTC of each bar's day.
    """
    return pd.DatetimeIndex(pd.to_datetime(bars["ts"], utc=True)).normalize() + DECISION


def attention_z(articles: tuple[str, ...], window: int, decisions: pd.DatetimeIndex) -> np.ndarray:
    """Market attention at each decision: the highest daily attention score among the days newly published.

    A day's attention score is the mean over articles of each article's robust z-score of log views against its own
    previous ``window`` days.

    Args:
        articles: Wikipedia titles.
        window: Baseline days.
        decisions: Decision times.

    Returns:
        The score per decision (NaN when nothing new was published or before the baselines exist).
    """
    zs, frames = [], []
    for a in articles:
        df = series("wiki", _wiki_name(a))
        x = np.log1p(df["views"].to_numpy(float))
        zs.append(pd.Series(detect.robust_z(x, window), index=df["ts"]))
        frames.append(df[["ts", "published_at"]])
    z = pd.concat(zs, axis=1).mean(axis=1, skipna=False).sort_index()
    pub = pd.concat(frames).groupby("ts", as_index=False)["published_at"].max()  # a day counts once all are out
    return newly_known(pub, z, decisions, "up")


def tone_z(window: int, decisions: pd.DatetimeIndex) -> np.ndarray:
    """GDELT tone of stock-market news at each decision: the lowest daily score among the days newly published.

    Args:
        window: Baseline days.
        decisions: Decision times.

    Returns:
        Robust z-scores of the daily average tone (negative: gloomier than usual); NaN when nothing new. Days still
        running when fetched are left out.
    """
    df = series("gdelt", store.slug(TONE_QUERY))
    df = df[~df["partial"]].dropna(subset=["tone"])
    z = pd.Series(detect.robust_z(df["tone"].to_numpy(float), window), index=df["ts"])
    return newly_known(df, z, decisions, "down")


def derisk(z: np.ndarray, threshold: float, hold: int, side: str) -> np.ndarray:
    """Positions of a de-risk rule: invested, except for ``hold`` decisions from each spike on.

    Args:
        z: Signal score per decision (NaN: nothing new).
        threshold: Spike level in robust standard deviations.
        hold: Decisions spent out after a spike (the spike's own included).
        side: ``up`` (spikes above) or ``down`` (drops below).

    Returns:
        1.0 invested, 0.0 out. Invested while the score doesn't exist yet, like the benchmark.
    """
    zz = np.nan_to_num(np.asarray(z, dtype=float), nan=0.0)
    spike = (zz >= threshold) if side == "up" else (zz <= -threshold)
    recent = pd.Series(spike.astype(float)).rolling(max(int(hold), 1), min_periods=1).max().to_numpy()
    return 1.0 - recent


def _signal_z(bars: pd.DataFrame, window: int) -> np.ndarray | None:
    """The robust z-score of a synthetic ``signal`` column, when the bars carry one (calibration and tests).

    Args:
        bars: Bars, possibly with ``signal`` (the value known at each bar's decision).
        window: Baseline bars.

    Returns:
        Scores, or None when the bars have no signal column.
    """
    if "signal" not in bars:
        return None
    return detect.robust_z(bars["signal"].to_numpy(float), window)


def attention_rule(rule: str, bars: pd.DataFrame, window: int, threshold: float, hold: int) -> pd.Series:
    """An attention de-risk rule's positions.

    Args:
        rule: ``attention_derisk`` or ``crypto_attention_derisk`` (picks the article basket).
        bars: Normalised daily bars (a ``signal`` column replaces the stored views).
        window: Baseline days.
        threshold: Spike level.
        hold: Days out after a spike.

    Returns:
        Positions aligned with ``bars``.
    """
    z = _signal_z(bars, window)
    if z is None:
        z = attention_z(ATTENTION[rule], window, _decisions(bars))
    return pd.Series(derisk(z, threshold, hold, "up"), index=bars.index)


def tone_rule(bars: pd.DataFrame, window: int, threshold: float, hold: int) -> pd.Series:
    """The tone de-risk rule's positions: out after news tone drops below its usual level.

    Args:
        bars: Normalised daily bars (a ``signal`` column replaces the stored tone).
        window: Baseline days.
        threshold: Drop level.
        hold: Days out after a drop.

    Returns:
        Positions aligned with ``bars``.
    """
    z = _signal_z(bars, window)
    if z is None:
        z = tone_z(window, _decisions(bars))
    return pd.Series(derisk(z, threshold, hold, "down"), index=bars.index)


def synthetic_signal_bars(years: float, planted: float = 0.0, seed: int = 0, side: str = "up",
                          vol: float = 0.16, drift: float = 0.06) -> pd.DataFrame:
    """Daily bars with a signal column: a GARCH market and a spiky attention (or tone) series.

    Log return per day: ``drift/252 + σ_t b_t ε_t``, with ``σ_t`` GARCH(1,1) (α 0.08, β 0.90) around ``vol``, ``ε_t``
    Student t (5 degrees of freedom, unit variance) and ``b_t`` the planted burst factor. The signal is an AR(1)
    level (φ 0.9, daily shocks 0.1) plus spikes that arrive about once a month, each of size 1 + Exp(1) and halving
    every day; for ``side="down"`` the spikes are drops. With ``planted`` above zero, volatility is multiplied by
    ``1 + 9 · planted`` on the five days after each spike: a de-risk rule that steps out after spikes avoids them.

    Args:
        years: Length in years of 252 days.
        planted: Burst size (0 gives a signal unrelated to the market: a true edge of exactly zero).
        seed: Random seed.
        side: ``up`` (attention spikes) or ``down`` (tone drops).
        vol: Long-run annual volatility.
        drift: Annual drift (the same on every day, so stepping out of high-volatility days improves the Sharpe).

    Returns:
        Normalised bars from 1990-01-02 on business days, with ``signal``.
    """
    rng = np.random.default_rng(seed)
    n = int(years * 252)
    spikes = rng.random(n) < SPIKE_RATE
    sizes = np.where(spikes, 1.0 + rng.exponential(1.0, n), 0.0)
    level, decay = np.empty(n), np.empty(n)
    a = d = 0.0
    shocks = rng.normal(0.0, 0.1, n)
    for i in range(n):
        a = 0.9 * a + shocks[i]
        d = 0.5 * d + sizes[i]
        level[i], decay[i] = a, d
    signal = level + (decay if side == "up" else -decay)
    burst = np.ones(n)
    for i in np.flatnonzero(spikes):
        burst[i + 1: i + 1 + BURST_DAYS] = 1.0 + BURST_SCALE * planted
    alpha, beta = 0.08, 0.90
    var_lr = vol**2 / 252
    omega = var_lr * (1 - alpha - beta)
    eps = rng.standard_t(5, n) / math.sqrt(5 / 3)
    v, var = var_lr, np.empty(n)
    for i in range(n):
        var[i] = v
        v = omega + alpha * v * eps[i] ** 2 + beta * v
    sd = np.sqrt(var) * burst
    log_r = drift / 252 + sd * eps
    close = 100.0 * np.exp(np.cumsum(log_r))
    open_ = close / np.exp(0.7 * log_r)
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.4, n)) * sd)
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.4, n)) * sd)
    ts = pd.bdate_range("1990-01-02", periods=n, tz="UTC")
    return normalize_bars(pd.DataFrame({"ts": ts, "open": open_, "high": high, "low": low, "close": close,
                                        "volume": 1e6, "signal": signal}))


def _hac(x: np.ndarray, y: np.ndarray, lags: int) -> tuple[np.ndarray, np.ndarray, float]:
    """OLS with Newey-West standard errors.

    Args:
        x: Regressors with a constant column, shape (n, k).
        y: Target, shape (n,).
        lags: Newey-West lags (Bartlett weights).

    Returns:
        Coefficients, their HAC standard errors, and R squared.
    """
    xtx_inv = np.linalg.inv(x.T @ x)
    b = xtx_inv @ x.T @ y
    e = y - x @ b
    u = x * e[:, None]
    s = u.T @ u
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1)
        g = u[lag:].T @ u[:-lag]
        s += w * (g + g.T)
    cov = xtx_inv @ s @ xtx_inv
    r2 = 1.0 - float(e @ e) / float(((y - y.mean()) ** 2).sum())
    return b, np.sqrt(np.diag(cov)), r2


def har_test(bars: pd.DataFrame, z: np.ndarray, start: str, end: str, expect: str, lags: int = 5) -> dict[str, Any]:
    """Whether a signal known at each decision adds to a HAR model of next-day volatility.

    Volatility is the Parkinson (1980) range estimator, log(ln(high/low)² / (4 ln 2)). The HAR model (Corsi, 2009)
    regresses tomorrow's log volatility on today's, the last week's mean and the last month's mean; the test adds the
    signal's score and asks whether its coefficient has the expected sign, with Newey-West errors.

    Args:
        bars: Normalised daily bars of the instrument.
        z: The signal's score at each bar's decision (aligned with ``bars``); NaN (nothing new) carries the last.
        start: First day of the sample (YYYY-MM-DD).
        end: Day after the last (the lockbox's first day).
        expect: ``positive`` (attention) or ``negative`` (tone).
        lags: Newey-West lags.

    Returns:
        ``n``, ``coef``, ``se``, ``t``, ``p_one_sided`` (normal approximation), ``r2_har``, ``r2_with`` and the
        sample's ``start`` and ``end``.
    """
    from scipy.stats import norm

    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts"], utc=True))
    rng = np.log(bars["high"].to_numpy(float) / bars["low"].to_numpy(float))
    lv = pd.Series(np.log(np.maximum(rng**2 / (4 * math.log(2)), 1e-12)), index=ts)
    df = pd.DataFrame({"y": lv.shift(-1), "d": lv, "w": lv.rolling(5).mean(), "m": lv.rolling(22).mean(),
                       "z": pd.Series(np.asarray(z, dtype=float)).ffill().to_numpy()}, index=ts)
    df = df[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index < pd.Timestamp(end, tz="UTC"))].dropna()
    y = df["y"].to_numpy()
    base = np.column_stack([np.ones(len(df)), df[["d", "w", "m"]].to_numpy()])
    full = np.column_stack([base, df["z"].to_numpy()])
    _b0, _s0, r2_0 = _hac(base, y, lags)
    b, s, r2 = _hac(full, y, lags)
    t = float(b[-1] / s[-1])
    p = float(norm.sf(t)) if expect == "positive" else float(norm.cdf(t))
    return {"n": int(len(df)), "coef": float(b[-1]), "se": float(s[-1]), "t": t, "p_one_sided": p,
            "r2_har": r2_0, "r2_with": r2, "start": str(df.index[0].date()) if len(df) else start,
            "end": str(df.index[-1].date()) if len(df) else end}
