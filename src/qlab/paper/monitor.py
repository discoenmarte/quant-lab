"""Forward statistics for the paper book: what the research period expects, and whether the forward days agree.

Design:

- **Expectations are frozen when a card enters the book**, from its research period only (``reference``). Nothing
  about the forward days changes them, so the bands are a prediction made in advance, not a fit.
- **Edge uses the research beta.** Forward edge is ``(net − rf) − β·(benchmark − rf)`` with the β measured on the
  research period. With a few weeks of data a β estimated on the forward days would soak up most of the return and
  leave an edge of noise.
- **Bands** are ±1.645 standard deviations (a 90% two-sided band, the one-sided 95% the lockbox uses on each side).
  Daily returns with lag-1 autocorrelation ρ have a long-run variance ``(1 + ρ)/(1 − ρ)`` times the daily one, so
  cumulative bands are widened by that factor.
- **The decay alarm** is Page's (1954) one-sided CUSUM on standardised forward edge, tuned to detect the edge
  falling from its research mean to zero: ``S_t = max(0, S_{t−1} − z_t − k)`` with ``z_t = (e_t − μ0)/σ0`` and
  ``k = μ0/(2σ0)``. The threshold ``h`` is set so that a strategy whose edge is exactly as researched raises a false
  alarm once every ten years on average, using Siegmund's (1985) approximation of the average run length.
- **Power is low and the module says so.** An edge Sharpe of 0.33 is 0.02 standard deviations a day; telling it
  from zero takes decades of daily data (``evidence_days``) and the alarm needs years to fire when an edge of that
  size disappears (``arl1_days``). The alarm catches breaks and large decay, not small ones.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from ..backtest import metrics
from ..gauntlet import stats

Z = 1.6448536269514722
ALARM_ARL0_YEARS = 10.0
SIEGMUND_SHIFT = 1.166  # Siegmund (1985): the boundary correction for a normal random walk, 2 · 0.583


def ar1_factor(rho: float) -> float:
    """Long-run variance multiplier of an AR(1) series.

    Args:
        rho: Lag-1 autocorrelation, already clipped to (-1, 1).

    Returns:
        ``(1 + ρ) / (1 − ρ)``; 1.0 for an independent series.
    """
    return (1.0 + rho) / (1.0 - rho)


def reference_from(net: np.ndarray, bench: np.ndarray, rf: np.ndarray, dpy: int) -> dict[str, float]:
    """What the research period says about a strategy's daily returns.

    Args:
        net: Net daily returns on the research period.
        bench: Benchmark daily returns, aligned.
        rf: Risk-free daily returns, aligned.
        dpy: Days per year.

    Returns:
        ``beta``; the edge's daily ``edge_mean``, ``edge_sd``, ``edge_skew``, ``edge_kurt`` (raw) and ``edge_rho``;
        the mean, standard deviation and lag-1 autocorrelation of daily log net returns (``log_mean``, ``log_sd``,
        ``log_rho``); ``edge_sharpe`` (annual); ``dpy`` and ``n_days``.
    """
    e, beta = metrics.edge(net, bench, rf)
    log_net = np.log1p(np.asarray(net, dtype=float))
    skew, kurt, rho = stats.moments(e)
    _, _, log_rho = stats.moments(log_net)
    sr = stats.sharpe(e)
    return {
        "beta": float(beta),
        "edge_mean": float(np.nanmean(e)),
        "edge_sd": float(np.nanstd(e, ddof=1)),
        "edge_skew": skew,
        "edge_kurt": kurt,
        "edge_rho": rho,
        "log_mean": float(np.mean(log_net)),
        "log_sd": float(np.std(log_net, ddof=1)),
        "log_rho": log_rho,
        "edge_sharpe": float(sr * math.sqrt(dpy)) if math.isfinite(sr) else float("nan"),
        "dpy": int(dpy),
        "n_days": int(len(net)),
    }


def forward_edge(net: np.ndarray, bench: np.ndarray, rf: np.ndarray, beta: float) -> np.ndarray:
    """Daily edge on forward days, with the research beta held fixed.

    Args:
        net: Net daily returns.
        bench: Benchmark daily returns.
        rf: Risk-free daily returns.
        beta: Beta measured on the research period.

    Returns:
        ``(net − rf) − β·(bench − rf)`` per day.
    """
    return (np.asarray(net) - rf) - beta * (np.asarray(bench) - rf)


def equity_band(n: int, log_mean: float, log_sd: float, log_rho: float = 0.0, z: float = Z) -> dict[str, np.ndarray]:
    """Expected growth of one unit after 1..n days, and the band around it.

    Args:
        n: Number of forward days.
        log_mean: Mean daily log return on the research period.
        log_sd: Standard deviation of daily log returns.
        log_rho: Lag-1 autocorrelation of daily log returns.
        z: Band half-width in standard deviations.

    Returns:
        ``mid``, ``lo`` and ``hi``: equity levels (1.0 = start) for days 1..n.
    """
    k = np.arange(1, n + 1, dtype=float)
    spread = z * log_sd * np.sqrt(k * ar1_factor(log_rho))
    return {"mid": np.exp(k * log_mean), "lo": np.exp(k * log_mean - spread), "hi": np.exp(k * log_mean + spread)}


def edge_band(n: int, mean: float, sd: float, rho: float = 0.0, z: float = Z) -> dict[str, np.ndarray]:
    """Expected cumulative edge after 1..n days, and the band around it.

    Args:
        n: Number of forward days.
        mean: Mean daily edge on the research period.
        sd: Standard deviation of daily edge.
        rho: Lag-1 autocorrelation of daily edge.
        z: Band half-width in standard deviations.

    Returns:
        ``mid``, ``lo`` and ``hi``: summed daily edge (0.01 = one percent) for days 1..n.
    """
    k = np.arange(1, n + 1, dtype=float)
    spread = z * sd * np.sqrt(k * ar1_factor(rho))
    return {"mid": k * mean, "lo": k * mean - spread, "hi": k * mean + spread}


def sharpe_band(sr: float, n: int, skew: float, kurt: float, rho: float, z: float = Z) -> tuple[float, float]:
    """Where an n-day Sharpe estimate should fall if the research Sharpe is the truth.

    The same band the lockbox stage uses: the research Sharpe plus or minus ``z`` standard errors, with the standard
    error corrected for skew, fat tails and autocorrelation (``stats.sr_variance``).

    Args:
        sr: Research Sharpe per day.
        n: Number of forward days.
        skew: Skewness of the research returns.
        kurt: Raw kurtosis of the research returns.
        rho: Lag-1 autocorrelation of the research returns.
        z: Band half-width in standard errors.

    Returns:
        ``(floor, ceiling)`` per day; NaN with fewer than two days.
    """
    v = stats.sr_variance(sr, n, skew, kurt, rho)
    if not math.isfinite(v):
        return float("nan"), float("nan")
    return sr - z * math.sqrt(v), sr + z * math.sqrt(v)


def evidence_days(sr: float, skew: float, kurt: float, rho: float, z: float = Z) -> float:
    """Forward days needed before the band's floor clears zero, if the research Sharpe is the truth.

    Args:
        sr: Research Sharpe per day.
        skew: Skewness of the research returns.
        kurt: Raw kurtosis.
        rho: Lag-1 autocorrelation.
        z: Band half-width in standard errors.

    Returns:
        Days, or infinity when the research Sharpe isn't positive.
    """
    if not (math.isfinite(sr) and sr > 0):
        return float("inf")
    per_day = stats.sr_variance(sr, 2, skew, kurt, rho) * 2  # variance of one observation's contribution
    return float(z * z * per_day / (sr * sr)) if math.isfinite(per_day) else float("inf")


def siegmund_arl(drift: float, h: float) -> float:
    """Average run length of a one-sided CUSUM on unit-variance normal increments.

    Siegmund (1985), "Sequential Analysis": ``ARL ≈ (exp(−2Δb) + 2Δb − 1) / (2Δ²)`` with
    ``b = h + 1.166`` and Δ the mean of the increments ``−z_t − k``; ``b²`` when Δ = 0.

    Args:
        drift: Mean increment Δ (negative in control).
        h: Alarm threshold in standard deviations.

    Returns:
        Expected number of days until the statistic first exceeds ``h``; infinity on overflow.
    """
    b = h + SIEGMUND_SHIFT
    if abs(drift) < 1e-12:
        return b * b
    try:
        return (math.exp(-2.0 * drift * b) + 2.0 * drift * b - 1.0) / (2.0 * drift * drift)
    except OverflowError:
        return float("inf")


def cusum_threshold(k: float, arl0: float) -> float:
    """Alarm threshold giving one false alarm every ``arl0`` days on average while the edge is as researched.

    Args:
        k: Reference value: half the shift to detect, in standard deviations of daily edge.
        arl0: Target in-control average run length, in days.

    Returns:
        ``h`` in standard deviations, at least 0.
    """
    if siegmund_arl(-k, 0.0) >= arl0:
        return 0.0
    hi = 1.0
    while siegmund_arl(-k, hi) < arl0:
        hi *= 2.0
    return float(brentq(lambda h: siegmund_arl(-k, h) - arl0, 0.0, hi, xtol=1e-6))


def cusum(e: np.ndarray, mean: float, sd: float, k: float) -> np.ndarray:
    """Lower one-sided CUSUM of daily edge: evidence, accumulated, that the mean has fallen below ``mean``.

    Args:
        e: Daily forward edge.
        mean: In-control mean (the research mean).
        sd: In-control standard deviation.
        k: Reference value in standard deviations.

    Returns:
        The statistic after each day, never below zero.
    """
    out = np.empty(len(e))
    s = 0.0
    for i, x in enumerate(np.asarray(e, dtype=float)):
        s = max(0.0, s - (x - mean) / sd - k)
        out[i] = s
    return out


def decay_alarm(e: np.ndarray, ref: dict[str, float], arl0_years: float = ALARM_ARL0_YEARS) -> dict[str, object]:
    """Run the decay alarm on forward edge against the frozen research expectation.

    Args:
        e: Daily forward edge (research beta).
        ref: Output of ``reference_from``.
        arl0_years: Years between false alarms, on average, while the edge is as researched.

    Returns:
        ``applicable`` and, when it is, ``k``, ``h``, ``arl0_days``, ``arl1_days`` (expected days to an alarm if the
        edge fell to zero on day one), ``series`` (the statistic per day), ``value`` (latest), ``alarm`` and
        ``first_alarm`` (index of the first day above ``h``, or None). Without a positive research edge there is no
        edge to decay, and ``reason`` says so.
    """
    mean, sd, dpy = ref["edge_mean"], ref["edge_sd"], int(ref["dpy"])
    if not (math.isfinite(mean) and math.isfinite(sd) and sd > 0 and mean > 0):
        return {"applicable": False, "reason": "The research edge isn't positive, so there is no edge to decay.",
                "series": [], "alarm": False, "first_alarm": None}
    k = mean / (2.0 * sd)
    arl0 = arl0_years * dpy
    h = cusum_threshold(k, arl0)
    path = cusum(e, mean, sd, k)
    above = np.flatnonzero(path > h)
    return {
        "applicable": True,
        "k": k,
        "h": h,
        "arl0_days": arl0,
        "arl1_days": siegmund_arl(k, h),
        "series": path,
        "value": float(path[-1]) if len(path) else 0.0,
        "alarm": bool(len(above)),
        "first_alarm": int(above[0]) if len(above) else None,
    }
