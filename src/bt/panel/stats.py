"""P3 — anti-overfit statistics: DSR, PSR, PBO (CSCV), BH-FDR.

Pure NumPy/`math` (no scipy in this env). Spec §7.

References (formulas verified against, not reconstructed from memory):

  * Bailey, D. & López de Prado, M. (2014). "The Deflated Sharpe Ratio:
    Correcting for Selection Bias, Backtest Overfitting and Non-Normality."
    *Journal of Portfolio Management*, 40(5).
      PSR(SR*) = Φ( (SR_hat − SR*)·√(T−1)
                    / √(1 − γ₃·SR_hat + ((γ₄−1)/4)·SR_hat²) )
    where SR_hat is the per-observation (non-annualized) Sharpe, γ₃ skew, γ₄ raw
    kurtosis (3 for a normal), T = number of return observations.
    DSR = PSR(SR0) with the deflation benchmark
      SR0 = √V̂ · [ (1−γ)·Z⁻¹(1 − 1/N) + γ·Z⁻¹(1 − 1/(N·e)) ]
    γ = Euler–Mascheroni ≈ 0.5772156649, V̂ = variance of the N trial Sharpes,
    N = number of trials, e = Euler's number, Z⁻¹ = inverse standard-normal CDF.

  * Bailey, Borwein, López de Prado & Zhu (2017). "The Probability of Backtest
    Overfitting." *Journal of Computational Finance*, 20(4). PBO via CSCV:
    split T into S even blocks, for each of the C(S, S/2) combinations form an
    IS set (half the blocks) and its OOS complement, pick the IS-best config,
    record its OOS performance rank → logit; PBO = fraction of combinations with
    logit ≤ 0 (IS-best lands in the OOS lower half).
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Sequence

import numpy as np

_EULER_GAMMA = 0.5772156649015329
_E = math.e


# ---------------------------------------------------------------------------
# standard-normal CDF / inverse CDF (no scipy)
# ---------------------------------------------------------------------------
def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, then one
    Halley refinement step → ~1e-9 absolute accuracy on (0,1))."""
    if not (0.0 < p < 1.0):
        if p <= 0.0:
            return -math.inf
        return math.inf

    # Acklam coefficients
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    plow, phigh = 0.02425, 1.0 - 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)

    # one Halley step for full double precision
    e = _norm_cdf(x) - p
    u = e * math.sqrt(2.0 * math.pi) * math.exp(x * x / 2.0)
    x = x - u / (1.0 + x * u / 2.0)
    return x


# ---------------------------------------------------------------------------
# Sharpe moments
# ---------------------------------------------------------------------------
def _sharpe_and_moments(returns: np.ndarray) -> tuple[float, float, float, int]:
    """Return (per-bar Sharpe, skew γ₃, raw kurtosis γ₄, T).

    Sharpe uses ddof=1 std (sample). Skew/kurtosis use population (ddof=0)
    moments standardized by the population std — the convention in the DSR
    paper's PSR denominator.
    """
    r = np.asarray(returns, dtype=np.float64)
    T = r.size
    if T < 2:
        raise ValueError("need >= 2 returns for a Sharpe ratio")
    sd_sample = r.std(ddof=1)
    if sd_sample == 0.0:
        return 0.0, 0.0, 3.0, T
    sr = float(r.mean() / sd_sample)
    m = r.mean()
    sp = r.std(ddof=0)
    g3 = float(((r - m) ** 3).mean() / sp ** 3)
    g4 = float(((r - m) ** 4).mean() / sp ** 4)
    return sr, g3, g4, T


# ---------------------------------------------------------------------------
# PSR / DSR
# ---------------------------------------------------------------------------
def probabilistic_sharpe_ratio(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """Probabilistic Sharpe Ratio PSR(SR*) ∈ [0,1] (Bailey & LdP 2014).

    Probability the *true* (per-bar) Sharpe exceeds ``sr_benchmark`` given the
    observed sample, accounting for length, skew and kurtosis.
    """
    sr, g3, g4, T = _sharpe_and_moments(returns)
    denom = math.sqrt(max(1e-300, 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2))
    z = (sr - sr_benchmark) * math.sqrt(T - 1) / denom
    return _norm_cdf(z)


def expected_max_sharpe(trial_sharpes: Sequence[float]) -> float:
    """Deflation benchmark SR0 = expected maximum of N i.i.d. trial Sharpes.

    SR0 = √V̂ · [ (1−γ)·Z⁻¹(1 − 1/N) + γ·Z⁻¹(1 − 1/(N·e)) ], with V̂ the variance
    of the trial Sharpes (per-bar units). N==1 → V̂ has no dispersion → SR0=0.
    """
    s = np.asarray(list(trial_sharpes), dtype=np.float64)
    N = s.size
    if N <= 1:
        return 0.0
    v_hat = float(s.var(ddof=1))
    if v_hat <= 0.0:
        return 0.0
    z1 = _norm_ppf(1.0 - 1.0 / N)
    z2 = _norm_ppf(1.0 - 1.0 / (N * _E))
    return math.sqrt(v_hat) * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def deflated_sharpe_ratio(
    returns: np.ndarray, trial_sharpes: Sequence[float]
) -> float:
    """Deflated Sharpe Ratio DSR ∈ [0,1] = PSR(SR0) where SR0 is the expected
    maximum Sharpe across the trial set (Bailey & LdP 2014).

    ``returns`` are the candidate strategy's per-bar returns; ``trial_sharpes``
    are the per-bar Sharpe ratios of ALL configurations tried (the multiple-
    testing set). More trials / wider dispersion → larger SR0 → smaller DSR.
    """
    sr0 = expected_max_sharpe(trial_sharpes)
    return probabilistic_sharpe_ratio(returns, sr_benchmark=sr0)


# ---------------------------------------------------------------------------
# Benjamini-Hochberg FDR
# ---------------------------------------------------------------------------
def bh_fdr(pvalues: Sequence[float], alpha: float = 0.05):
    """Benjamini-Hochberg FDR control.

    Returns ``(reject, adjusted)`` aligned to the input order. ``reject[i]`` is
    True if hypothesis i is rejected at FDR ``alpha``; ``adjusted[i]`` is the
    BH-adjusted p-value (q-value), monotone non-decreasing in sorted-p order.
    """
    p = np.asarray(list(pvalues), dtype=np.float64)
    m = p.size
    if m == 0:
        return [], []
    order = np.argsort(p)
    ranked = p[order]
    ranks = np.arange(1, m + 1)

    # adjusted q-values: cumulative-min from the largest rank downward
    raw_adj = ranked * m / ranks
    adj_sorted = np.minimum.accumulate(raw_adj[::-1])[::-1]
    adj_sorted = np.clip(adj_sorted, 0.0, 1.0)

    # reject: largest k with ranked[k] <= k/m * alpha → reject all up to k
    below = ranked <= (ranks / m) * alpha
    if below.any():
        kmax = np.max(np.where(below)[0])
        reject_sorted = np.arange(m) <= kmax
    else:
        reject_sorted = np.zeros(m, dtype=bool)

    adjusted = np.empty(m)
    reject = np.empty(m, dtype=bool)
    adjusted[order] = adj_sorted
    reject[order] = reject_sorted
    return reject.tolist(), adjusted.tolist()


# ---------------------------------------------------------------------------
# PBO via CSCV
# ---------------------------------------------------------------------------
def pbo(perf_matrix: np.ndarray, n_splits: int = 16) -> float:
    """Probability of Backtest Overfitting via CSCV (Bailey et al. 2017).

    Args:
        perf_matrix: ``[T, C]`` per-bar performance series, one column per
            configuration tried (e.g. per-bar returns; the IS/OOS score is the
            Sharpe of each column over the respective block set).
        n_splits: number of equal time blocks S (must be even); CSCV forms all
            ``C(S, S/2)`` IS/OOS partitions.

    Returns:
        PBO ∈ [0,1]: fraction of partitions where the IS-best configuration
        lands in the lower half of the OOS ranking (logit ≤ 0).
    """
    M = np.asarray(perf_matrix, dtype=np.float64)
    T, C = M.shape
    if C < 2:
        return 0.0
    S = n_splits if n_splits % 2 == 0 else n_splits - 1
    S = max(2, min(S, T))
    # even block split
    block_bounds = np.array_split(np.arange(T), S)
    blocks = [b for b in block_bounds if b.size > 0]
    S = len(blocks)
    if S < 2:
        return 0.0

    def _sharpe_cols(rows: np.ndarray) -> np.ndarray:
        sub = M[rows]
        mu = sub.mean(axis=0)
        sd = sub.std(axis=0, ddof=1) if sub.shape[0] > 1 else np.ones(C)
        sd = np.where(sd == 0.0, np.nan, sd)
        sr = mu / sd
        return np.nan_to_num(sr, nan=0.0)

    half = S // 2
    logits = []
    block_idx = list(range(S))
    for is_blocks in combinations(block_idx, half):
        is_set = set(is_blocks)
        is_rows = np.concatenate([blocks[i] for i in block_idx if i in is_set])
        oos_rows = np.concatenate([blocks[i] for i in block_idx if i not in is_set])

        is_sr = _sharpe_cols(is_rows)
        oos_sr = _sharpe_cols(oos_rows)

        best = int(np.argmax(is_sr))
        # OOS relative rank of the IS-best config in [0,1] (fraction below it)
        oos_rank = float((oos_sr < oos_sr[best]).sum()) / (C - 1) if C > 1 else 0.5
        # guard the open interval for the logit
        w = min(max(oos_rank, 1.0 / (C + 1)), 1.0 - 1.0 / (C + 1))
        logits.append(math.log(w / (1.0 - w)))

    if not logits:
        return 0.0
    return float(np.mean(np.asarray(logits) <= 0.0))
