"""Anomaly detectors, and thresholds set from history for a stated false-alarm budget.

Three detectors, each a statistic per observation that uses only the past (decision 0016):

- **Robust z-score** (``robust_z``): how far today's value is from the median of the previous ``window``
  observations, in units of their median absolute deviation (scaled by 1.4826 to match a standard deviation under
  normality). Medians ignore the outliers they are meant to find (Rousseeuw & Croux, 1993). Catches spikes.
- **CUSUM** (``cusum``): Page's (1954) cumulative sum of robust z-scores beyond a slack ``k``, reset after each alarm.
  Catches a persistent shift too small to stand out on any one day.
- **Bayesian online change points** (``bocpd``): Adams & MacKay (2007) with a Gaussian of unknown mean and variance.
  The statistic is the posterior probability that the current regime began within the last ``recent``
  observations. Catches a change in level or volatility, with a probability rather than a distance.

Thresholds come from the series' own history, never from what would have made money (``calibrate``):

- **Budget.** An alarm *episode* starts when the statistic crosses its threshold after at least ``COOLDOWN``
  observations below it. The threshold is set so that the history holds the budgeted number of episodes: by default
  one a month per series and detector for a *notable* anomaly and one a year for a *severe* one. It is also never
  below the level that 5% (notable) or 0.5% (severe) of observations exceed, so a slow series with long episodes
  can't turn an ordinary day into an anomaly.
- **Measured false-alarm rate.** History includes real events, so its rate is the budget by construction. The false
  rate is measured on null histories with no events: the same values in random order (same distribution, fat tails
  included, nothing to detect). Reported per month.
- **Measured delay.** A step of three robust standard deviations (and one of one) is planted at a random point of a
  null history; the delay is the number of observations until the first alarm, and the detection rate counts alarms
  within ten.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.special import gammaln

DETECTORS = ("robust_z", "cusum", "bocpd")
MAD_SCALE = 1.4826
Z_CLIP = 8.0  # z-scores beyond this carry no more information and would let one day dominate CUSUM and BOCPD
CUSUM_K = 0.5  # slack: tuned to detect a shift of one standard deviation (Page, 1954; Montgomery, 2009)
HAZARD = 1 / 250  # prior: a change about once a year of daily data
RUN_MAX = 200  # run lengths kept by BOCPD; older mass is merged into the longest
RECENT = 5  # BOCPD's statistic: probability the regime began within this many observations
COOLDOWN = 5
NOTABLE_PER_MONTH = 1.0
SEVERE_PER_MONTH = 1.0 / 12
# At most this share of observations may sit above each threshold. Slow series (realised volatility, CUSUM sums) have
# long episodes, so an episode budget alone would put the threshold on ordinary days.
MAX_SHARE = {"notable": 0.05, "severe": 0.005}
NULL_REPS = 10
PLANT_REPS = 10
PLANT_SIZES = {"": 3.0, "_small": 1.0}  # a spike-sized step, and a subtle persistent one where CUSUM helps
DETECT_WITHIN = 10


def robust_z(x: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    """Robust z-score of each value against the previous ``window`` values.

    Args:
        x: Values in time order (NaN for missing).
        window: Past observations in the baseline.
        min_periods: Non-missing past values needed for a score; None means half the window.

    Returns:
        Scores aligned with ``x``; NaN without enough history or when the baseline has no spread at all.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    out = np.full(n, np.nan)
    need = max(5, window // 2) if min_periods is None else min_periods
    if n <= need:
        return out
    padded = np.concatenate([np.full(window, np.nan), x])
    past = sliding_window_view(padded[:-1], window)[:n]  # row t holds x[t-window .. t-1]
    enough = np.sum(~np.isnan(past), axis=1) >= need
    rows = np.flatnonzero(enough)
    if not len(rows):
        return out
    w = past[rows]
    med = np.nanmedian(w, axis=1)
    mad = MAD_SCALE * np.nanmedian(np.abs(w - med[:, None]), axis=1)
    sd = np.nanstd(w, axis=1)
    scale = np.where(mad > 0, mad, sd)  # a mostly constant baseline (counts of zero) falls back to the deviation
    with np.errstate(invalid="ignore", divide="ignore"):
        out[rows] = np.where(scale > 0, (x[rows] - med) / scale, np.nan)
    return out


def oriented(z: np.ndarray, direction: str) -> np.ndarray:
    """Scores turned so that larger means more anomalous in the direction watched.

    Args:
        z: Signed scores.
        direction: ``up`` (rises are anomalous), ``down`` (falls) or ``both``.

    Returns:
        ``z``, ``-z`` or ``|z|``.
    """
    return {"up": z, "down": -z}.get(direction, np.abs(z))


def cusum(z: np.ndarray, k: float = CUSUM_K, h: float | None = None, direction: str = "both") -> np.ndarray:
    """Page's CUSUM statistic on robust z-scores.

    ``S+ = max(0, S+ + z - k)`` and ``S- = max(0, S- - z - k)``; the statistic is the larger side allowed by
    ``direction``. With ``h`` set, both sides restart from zero after the statistic reaches ``h`` (each alarm is a
    fresh episode); without it, the sums run on (the shape used to calibrate ``h``).

    Args:
        z: Robust z-scores (NaN is skipped: the sums carry over).
        k: Slack in standard deviations.
        h: Reset level; None never resets.
        direction: ``up``, ``down`` or ``both``.

    Returns:
        The statistic per observation (NaN where ``z`` is NaN).
    """
    zc = np.clip(np.asarray(z, dtype=float), -Z_CLIP, Z_CLIP)
    out = np.full(len(zc), np.nan)
    hi = lo = 0.0
    for i, v in enumerate(zc):
        if np.isnan(v):
            continue
        hi = max(0.0, hi + v - k) if direction in ("up", "both") else 0.0
        lo = max(0.0, lo - v - k) if direction in ("down", "both") else 0.0
        out[i] = max(hi, lo)
        if h is not None and out[i] >= h:
            hi = lo = 0.0
    return out


def bocpd(z: np.ndarray, hazard: float = HAZARD, recent: int = RECENT, run_max: int = RUN_MAX,
          direction: str = "both") -> np.ndarray:
    """Posterior probability that the current regime began within the last ``recent`` observations.

    Adams & MacKay (2007): the run length (observations since the last change) has a posterior updated online.
    Each run's data are Gaussian with unknown mean and variance under a Normal-Inverse-Gamma prior (mean 0,
    kappa 1, alpha 1, beta 1, suited to standardised inputs), so the predictive is a Student t. The change
    hazard is constant. Run lengths beyond ``run_max`` are merged into the longest kept. Kappa and alpha depend only
    on the run length, so their terms are computed once.

    Args:
        z: Standardised values (robust z-scores, clipped at 8); NaN is skipped.
        hazard: Prior probability of a change at each observation.
        recent: Window of the statistic, in observations.
        run_max: Run lengths kept.
        direction: ``up`` or ``down`` count only changes whose recent values lie on that side of zero; ``both``
            counts any change.

    Returns:
        The probability per observation (NaN where ``z`` is NaN).
    """
    zc = np.clip(np.asarray(z, dtype=float), -Z_CLIP, Z_CLIP)
    out = np.full(len(zc), np.nan)
    r = np.arange(run_max)
    kappa = 1.0 + r  # kappa0 + run length
    alpha = 1.0 + r / 2  # alpha0 + run length / 2
    nu = 2 * alpha
    const = gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * np.log(nu * math.pi)
    kfac = (kappa + 1) / (alpha * kappa)
    half = (nu + 1) / 2
    p = np.zeros(run_max)
    mu = np.zeros(run_max)
    beta = np.ones(run_max)
    p[0], m = 1.0, 1
    recent_z: list[float] = []
    for i, x in enumerate(zc):
        if np.isnan(x):
            continue
        scale2 = beta[:m] * kfac[:m]
        logpred = const[:m] - 0.5 * np.log(scale2) - half[:m] * np.log1p((x - mu[:m]) ** 2 / (nu[:m] * scale2))
        w = p[:m] * np.exp(logpred - logpred.max())
        change = hazard * w.sum()
        new_m = min(m + 1, run_max)
        grown = (1 - hazard) * w
        beta_g = beta[:m] + kappa[:m] * (x - mu[:m]) ** 2 / (2 * (kappa[:m] + 1))
        mu_g = (kappa[:m] * mu[:m] + x) / (kappa[:m] + 1)
        if m == run_max:  # the longest run absorbs the one before it
            grown = np.concatenate([grown[:-2], [grown[-2] + grown[-1]]])
            beta_g, mu_g = beta_g[:-1], mu_g[:-1]
        p[1:new_m], mu[1:new_m], beta[1:new_m] = grown, mu_g, beta_g
        p[0], mu[0], beta[0] = change, 0.0, 1.0
        m = new_m
        p[:m] /= p[:m].sum()
        recent_z = (recent_z + [x])[-recent:]
        side = float(np.mean(recent_z))
        keep = direction == "both" or (side > 0 if direction == "up" else side < 0)
        out[i] = float(p[:recent].sum()) if keep else 0.0
    return out


def statistic(x: np.ndarray, detector: str, window: int, direction: str, h: float | None = None) -> np.ndarray:
    """A detector's statistic on a transformed series.

    Args:
        x: Transformed values (log views, a spread, a return...).
        detector: One of ``DETECTORS``.
        window: Baseline length for the robust z-score that every detector starts from.
        direction: ``up``, ``down`` or ``both``.
        h: CUSUM reset level (ignored by the others).

    Returns:
        Larger is more anomalous; NaN where undefined.

    Raises:
        ValueError: Unknown detector.
    """
    z = robust_z(x, window)
    if detector == "robust_z":
        return oriented(z, direction)
    if detector == "cusum":
        return cusum(z, h=h, direction=direction)
    if detector == "bocpd":
        return bocpd(z, direction=direction)
    raise ValueError(f"unknown detector {detector!r}")


def episodes(stat: np.ndarray, h: float, cooldown: int = COOLDOWN) -> np.ndarray:
    """Where alarm episodes start: the statistic reaches ``h`` after at least ``cooldown`` observations below it.

    Args:
        stat: Statistic per observation (NaN counts as below).
        h: Threshold.
        cooldown: Quiet observations needed before a new episode.

    Returns:
        Indices of episode starts.
    """
    above = np.nan_to_num(stat, nan=-np.inf) >= h
    starts, quiet = [], cooldown
    for i, a in enumerate(above):
        if a:
            if quiet >= cooldown:
                starts.append(i)
            quiet = 0
        else:
            quiet += 1
    return np.asarray(starts, dtype=int)


def threshold_for(stat: np.ndarray, target: int, cooldown: int = COOLDOWN) -> float:
    """The lowest threshold, coming down from the top, at which the history still holds at most ``target`` episodes.

    The episode count is not monotone in the threshold: very low thresholds merge everything into one long episode.
    So the search walks down a grid of upper quantiles (tail fractions from 1/n to one half) until the count first
    exceeds ``target``, then bisects between the last two grid points.

    Args:
        stat: Statistic over the history.
        target: Episodes allowed (at least 1).
        cooldown: As in ``episodes``.

    Returns:
        The threshold; +inf when the statistic is never defined.
    """
    vals = stat[np.isfinite(stat)]
    if not len(vals):
        return float("inf")
    tails = np.geomspace(1.0 / len(vals), 0.5, 60)
    grid = np.unique(np.quantile(vals, 1.0 - tails))[::-1]  # descending thresholds
    ok = float(grid[0])
    for h in grid:
        if len(episodes(stat, h, cooldown)) > target:
            hi, lo = ok, float(h)  # count(hi) <= target < count(lo)
            cands = np.unique(vals[(vals > lo) & (vals < hi)])
            a, b = 0, len(cands)
            while a < b:  # first candidate (ascending) whose count fits
                mid = (a + b) // 2
                if len(episodes(stat, cands[mid], cooldown)) <= target:
                    b = mid
                else:
                    a = mid + 1
            return float(cands[a]) if a < len(cands) else hi
        ok = float(h)
    return ok


def _cusum_stat(x: np.ndarray, window: int, direction: str, h: float) -> np.ndarray:
    """CUSUM with resets at ``h``, the form that runs live.

    Args:
        x: Transformed values.
        window: Baseline length.
        direction: Watched direction.
        h: Threshold and reset level.

    Returns:
        The statistic.
    """
    return cusum(robust_z(x, window), h=h, direction=direction)


def calibrate(x: np.ndarray, detector: str, window: int, direction: str, months: float,
              seed: int = 0, null_reps: int = NULL_REPS, plant_reps: int = PLANT_REPS) -> dict[str, Any]:
    """Thresholds for a detector on one series, and their measured false-alarm rate and delay.

    Args:
        x: Transformed history, oldest first.
        detector: One of ``DETECTORS``.
        window: Baseline length.
        direction: ``up``, ``down`` or ``both``.
        months: Calendar months the history spans.
        seed: Random seed for the null histories.
        null_reps: Shuffled histories for the false-alarm rate.
        plant_reps: Histories with a planted step for the delay.

    Returns:
        ``notable`` and ``severe`` thresholds; ``history_rate`` (episodes per month in history at the notable
        threshold, the budget by construction); ``null_rate`` and ``null_rate_severe`` (false alarms per month on
        shuffled histories); ``detect_rate`` and ``delay`` for a planted step of three robust standard deviations
        (share caught within ten observations at the notable threshold; median observations to the alarm, None if
        none caught); ``detect_rate_small`` and ``delay_small`` for a step of one; ``n_obs`` and ``months``.
    """
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(seed)
    stat = statistic(x, detector, window, direction)
    n_def = int(np.isfinite(stat).sum())
    months = max(months * n_def / max(len(x), 1), 1e-9)  # only the part where the statistic exists
    targets = {"notable": max(1, round(NOTABLE_PER_MONTH * months)), "severe": max(1, round(SEVERE_PER_MONTH * months))}

    def fit(st: np.ndarray, kind: str) -> float:
        vals = st[np.isfinite(st)]
        floor = float(np.quantile(vals, 1 - MAX_SHARE[kind])) if len(vals) else float("inf")
        return max(threshold_for(st, targets[kind]), floor)

    th = {k: fit(stat, k) for k in targets}
    if detector == "cusum":  # live CUSUM resets at its threshold; fit the threshold to that form
        for k in targets:
            h = th[k]
            for _ in range(4):
                h = fit(_cusum_stat(x, window, direction, h), k)
            th[k] = h
    live = (lambda v, h: _cusum_stat(v, window, direction, h)) if detector == "cusum" else (
        lambda v, _h: statistic(v, detector, window, direction))
    hist_rate = len(episodes(live(x, th["notable"]), th["notable"])) / months
    finite = x[np.isfinite(x)]
    null_n, null_s = [], []
    plants: dict[str, tuple[int, list[int]]] = {}
    if len(finite) > window + 20:
        sigma = MAD_SCALE * float(np.median(np.abs(finite - np.median(finite)))) or float(np.std(finite)) or 1.0
        for _ in range(null_reps):
            perm = rng.permutation(finite)
            null_n.append(len(episodes(live(perm, th["notable"]), th["notable"])) / months)
            null_s.append(len(episodes(live(perm, th["severe"]), th["severe"])) / months)
        sign = -1.0 if direction == "down" else 1.0
        for tag, size in PLANT_SIZES.items():
            caught, delays = 0, []
            for _ in range(plant_reps):
                perm = rng.permutation(finite)
                t0 = int(rng.integers(window + 10, len(perm) - DETECT_WITHIN - 1))
                perm[t0:] += sign * size * sigma
                s = live(perm, th["notable"])
                hit = np.flatnonzero(np.nan_to_num(s[t0: t0 + DETECT_WITHIN + 1], nan=-np.inf) >= th["notable"])
                if len(hit):
                    caught += 1
                    delays.append(int(hit[0]))
            plants[tag] = (caught, delays)
    out = {
        "notable": th["notable"], "severe": th["severe"], "history_rate": hist_rate,
        "null_rate": float(np.mean(null_n)) if null_n else None,
        "null_rate_severe": float(np.mean(null_s)) if null_s else None,
    }
    for tag in PLANT_SIZES:
        caught, delays = plants.get(tag, (0, []))
        out[f"detect_rate{tag}"] = caught / plant_reps if tag in plants else None
        out[f"delay{tag}"] = float(np.median(delays)) if delays else None
    return {**out, "n_obs": n_def, "months": months}
