"""Sharpe-ratio inference: standard errors, PSR, the luck threshold after N trials, DSR, MinTRL and the FDR.

Every Sharpe ratio in this module is **per period** (not annualised) unless the name says otherwise. Convert an
annual Sharpe with ``annual / sqrt(periods_per_year)`` and a variance with ``annual_var / periods_per_year``.

Method, following López de Prado, Lipton & Zoonekynd (2026), "Sharpe Ratio Inference: A New Standard for
Decision Making and Reporting", J. Portfolio Management 52(6):

- The sampling variance of the Sharpe ratio corrects for skewness, fat tails *and* first-order autocorrelation
  (``sr_variance``). Autocorrelation matters here: trend rules hold positions for weeks, and ignoring positive
  autocorrelation understates the error.
- Tests use the variance under the null hypothesis, as a hypothesis test should.
- Selection is handled two ways that answer different questions: the Deflated Sharpe Ratio (is this the best
  of N by luck?) and a Bayesian false-discovery rate (given how rare real edges are, how likely is this one to
  be false?).

Earlier references: Lo (2002) "The Statistics of Sharpe Ratios"; Bailey & López de Prado (2012) "The Sharpe
Ratio Efficient Frontier" (PSR, MinTRL); Bailey & López de Prado (2014) "The Deflated Sharpe Ratio".
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import norm

EULER_GAMMA = 0.5772156649015329


def sharpe(r: np.ndarray | pd.Series) -> float:
    """Per-period Sharpe ratio (mean over standard deviation).

    Args:
        r: Returns per period.

    Returns:
        The ratio, or NaN with fewer than two observations or no volatility.
    """
    a = np.asarray(r, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) < 2:
        return float("nan")
    sd = a.std(ddof=1)
    return float(a.mean() / sd) if sd > 0 else float("nan")


def moments(r: np.ndarray | pd.Series) -> tuple[float, float, float]:
    """Skewness, raw kurtosis (3.0 for a normal distribution) and lag-1 autocorrelation.

    Args:
        r: Returns per period.

    Returns:
        ``(skew, kurtosis, rho)``. ``rho`` is clipped to [-0.9, 0.9] so the variance formula stays defined;
        NaN inputs give the normal, independent values (0, 3, 0).
    """
    s = pd.Series(np.asarray(r, dtype=float)).dropna()
    if len(s) < 4 or s.std() == 0:
        return 0.0, 3.0, 0.0
    rho = s.autocorr(1)
    rho = 0.0 if not math.isfinite(rho) else float(np.clip(rho, -0.9, 0.9))
    return float(s.skew()), float(s.kurt() + 3.0), rho


def sr_variance(sr: float, n: int, skew: float = 0.0, kurt: float = 3.0, rho: float = 0.0) -> float:
    """Sampling variance of a per-period Sharpe estimate.

    ``(a − b·γ3·SR + c·(γ4 − 1)/4·SR²) / n`` with ``a = 1 + 2ρ/(1−ρ)``, ``b = 1 + ρ/(1−ρ) + ρ²/(1−ρ²)``,
    ``c = 1 + 2ρ²/(1−ρ²)`` for AR(1) returns (López de Prado, Lipton & Zoonekynd, 2026). With ρ = 0 it is the
    Bailey & López de Prado (2012) formula; with normal returns too it is Lo's (2002) ``(1 + SR²/2) / n``.

    Args:
        sr: Per-period Sharpe at which to evaluate the variance.
        n: Number of return observations.
        skew: Skewness of the returns.
        kurt: Raw kurtosis of the returns.
        rho: Lag-1 autocorrelation of the returns.

    Returns:
        The variance, or NaN when ``n < 2`` or the formula isn't positive.
    """
    if n < 2:
        return float("nan")
    a = 1 + 2 * rho / (1 - rho)
    b = 1 + rho / (1 - rho) + rho**2 / (1 - rho**2)
    c = 1 + 2 * rho**2 / (1 - rho**2)
    v = (a - b * skew * sr + c * (kurt - 1) / 4 * sr**2) / n
    return float(v) if v > 0 else float("nan")


def psr(sr: float, sr_star: float, n: int, skew: float = 0.0, kurt: float = 3.0, rho: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio: confidence that the true Sharpe exceeds ``sr_star``.

    ``Φ((SR − SR*) / σ(SR*))``, one minus the p-value of the test H0: true Sharpe = ``sr_star``.

    Args:
        sr: Observed per-period Sharpe.
        sr_star: Benchmark per-period Sharpe (the null).
        n: Number of return observations.
        skew: Skewness of the returns.
        kurt: Raw kurtosis of the returns.
        rho: Lag-1 autocorrelation of the returns.

    Returns:
        A probability in [0, 1], or NaN when undefined.
    """
    v = sr_variance(sr_star, n, skew, kurt, rho)
    if not (math.isfinite(sr) and math.isfinite(v)):
        return float("nan")
    return float(norm.cdf((sr - sr_star) / math.sqrt(v)))


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """Expected best per-period Sharpe among ``n_trials`` strategies with no skill.

    Bailey & López de Prado (2014), eq. 5: ``√V · ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))``, γ the
    Euler-Mascheroni constant. ``V`` is the variance of Sharpe ratios *across* the trials. With correlated
    trials that cross-sectional variance shrinks by (1 − ρ), which is exactly how much the expected maximum of
    equicorrelated estimates shrinks, so N needn't be adjusted separately for that case.

    Args:
        n_trials: Number of trials N.
        var_sr: Variance of the per-period Sharpe ratios across trials.

    Returns:
        The threshold SR0; 0 for a single trial or no variance.
    """
    if n_trials <= 1 or not math.isfinite(var_sr) or var_sr <= 0:
        return 0.0
    a = norm.ppf(1.0 - 1.0 / n_trials)
    b = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(var_sr) * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b))


VARIANCE_METHODS = ("sample", "upper_half", "mad", "luck_bound", "luck_max")
# Small-sample factors that make (1.4826 · MAD)² unbiased for a normal variance, by simulation (300,000 samples
# each); from 10 trials on, n / (n − 0.6) is within 1% of the simulated factor.
_MAD_FACTOR = {2: 0.909, 3: 1.318, 4: 1.395, 5: 1.103, 6: 1.154, 7: 1.055, 8: 1.086, 9: 1.034}


def trial_variance(sr: np.ndarray | list[float], method: str = "sample", luck: float = math.inf) -> float:
    """Variance of Sharpe ratios across trials, the ``V`` of the Deflated Sharpe, estimated by ``method``.

    The expected maximum of N lucky trials depends on how widely luck spreads their Sharpes. A trial whose Sharpe
    is far below the others, for example one that trades daily and pays its costs every day, can never be the
    maximum, but it widens the sample variance. The methods differ in how much such a trial counts:

    - ``sample``: the sample variance (ddof 1). Every trial counts fully.
    - ``upper_half``: the mean squared distance above the median, ``2 Σ_{x > m} (x − m)² / (N − 1)``. For a
      symmetric spread it estimates the variance (nearly unbiased from N = 2); trials below the median only move
      the median.
    - ``mad``: ``(1.4826 · MAD)²`` with a small-sample factor. Ignores outliers in both tails.
    - ``luck_bound``: the sample variance, but no more than ``luck``, the sampling variance of one Sharpe
      estimate with no edge: the spread of independent trials without skill. Extra spread comes from real
      differences between the trials, such as their costs.
    - ``luck_max``: the sample variance, but no more than ``luck · N / (N − 1)``. Trials without skill whose
      average correlation is ρ̄ spread ``(1 − ρ̄) · luck`` in expectation, and ρ̄ can't fall below −1/(N − 1),
      so this is the widest spread luck can produce whatever the correlations. Rules that bet against each
      other (a trend rule and a reversion rule) can spread wider than independent ones.

    Args:
        sr: Sharpe ratios of the trials, all in the same units (per period or annual).
        method: One of ``VARIANCE_METHODS``.
        luck: Sampling variance of one Sharpe estimate under the null, in the units of ``sr`` squared (the mean
            over trials when their lengths differ). Used by ``luck_bound`` and ``luck_max``; infinity means no
            bound.

    Returns:
        The variance; 0 with fewer than two finite Sharpes.

    Raises:
        ValueError: Unknown method.
    """
    if method not in VARIANCE_METHODS:
        raise ValueError(f"unknown variance method {method!r}; use one of {', '.join(VARIANCE_METHODS)}")
    x = np.asarray(sr, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return 0.0
    if method == "upper_half":
        m = float(np.median(x))
        return float(2.0 * np.sum((x[x > m] - m) ** 2) / (n - 1))
    if method == "mad":
        mad = float(np.median(np.abs(x - np.median(x))))
        return float((1.4826 * mad) ** 2 * _MAD_FACTOR.get(n, n / (n - 0.6)))
    v = float(np.var(x, ddof=1))
    if method == "luck_bound":
        return min(v, luck)
    if method == "luck_max":
        return min(v, luck * n / (n - 1))
    return v


def dsr(r: np.ndarray | pd.Series, n_trials: int, var_sr: float) -> dict[str, float]:
    """Deflated Sharpe Ratio: PSR against the best Sharpe expected from luck after ``n_trials``.

    Args:
        r: Per-period returns of the selected strategy.
        n_trials: Number of distinct trials (from the ledger).
        var_sr: Variance of the per-period Sharpe ratios across those trials.

    Returns:
        ``dsr`` (probability), ``sr`` (observed), ``sr0`` (luck threshold), ``n``, ``skew``, ``kurt``, ``rho``.
    """
    a = np.asarray(r, dtype=float)
    sr = sharpe(a)
    skew, kurt, rho = moments(a)
    sr0 = expected_max_sharpe(n_trials, var_sr)
    n = int(np.isfinite(a).sum())
    return {"dsr": psr(sr, sr0, n, skew, kurt, rho), "sr": sr, "sr0": sr0, "n": n, "skew": skew, "kurt": kurt,
            "rho": rho}


def false_discovery(
    sr: float, sr1: float, n: int, prior: float, skew: float = 0.0, kurt: float = 3.0, rho: float = 0.0
) -> float:
    """Posterior probability that a strategy with this Sharpe has no edge (the observed FDR).

    Two hypotheses: no edge (true Sharpe 0) with probability ``1 − prior``, or a real edge of size ``sr1``.
    Bayes' rule on the chance of seeing a Sharpe at least this high under each:
    ``P(H0 | ŜR ≥ sr) = p0 (1 − π) / (p0 (1 − π) + p1 π)`` (López de Prado, Lipton & Zoonekynd, 2026). Real
    edges are rare, so a low prior makes this number sobering: it's the honest answer to "is this real?".

    Args:
        sr: Observed per-period Sharpe.
        sr1: Per-period Sharpe of a real edge.
        n: Number of return observations.
        prior: Prior probability that a tested idea is real, in (0, 1).
        skew: Skewness of the returns.
        kurt: Raw kurtosis of the returns.
        rho: Lag-1 autocorrelation of the returns.

    Returns:
        A probability in [0, 1], or NaN when undefined.
    """
    p0 = 1.0 - psr(sr, 0.0, n, skew, kurt, rho)
    p1 = 1.0 - psr(sr, sr1, n, skew, kurt, rho)
    if not (math.isfinite(p0) and math.isfinite(p1)):
        return float("nan")
    denom = p0 * (1 - prior) + p1 * prior
    return float(p0 * (1 - prior) / denom) if denom > 0 else float("nan")


def min_track_record(
    sr: float, sr_star: float, skew: float = 0.0, kurt: float = 3.0, rho: float = 0.0, alpha: float = 0.05
) -> float:
    """Minimum number of observations for the Sharpe to beat ``sr_star`` at confidence 1 − alpha.

    Bailey & López de Prado (2012), eq. 11, with the autocorrelation-adjusted variance.

    Args:
        sr: Observed per-period Sharpe.
        sr_star: Benchmark per-period Sharpe.
        skew: Skewness of the returns.
        kurt: Raw kurtosis of the returns.
        rho: Lag-1 autocorrelation of the returns.
        alpha: Significance level.

    Returns:
        Observations needed; infinity when ``sr <= sr_star``.
    """
    if not math.isfinite(sr) or sr <= sr_star:
        return float("inf")
    unit = 2 * sr_variance(sr, 2, skew, kurt, rho)  # variance numerator, i.e. the variance for one observation
    if not math.isfinite(unit):
        return float("inf")
    return float(1.0 + unit * (norm.ppf(1.0 - alpha) / (sr - sr_star)) ** 2)
