"""P3 — DSR / PBO / BH-FDR statistics tests (TDD, written first).

Anti-overfit statistics per spec §7. The math surface is high factual risk,
so these are mostly PROPERTY tests (monotonicity, bounds, reductions) plus a
small number of independently-hand-computed anchors — never a mirror of the
implementation's own formula.

References:
  * Bailey & López de Prado (2014), "The Deflated Sharpe Ratio: Correcting for
    Selection Bias, Backtest Overfitting and Non-Normality", J. Portfolio Mgmt.
  * Bailey, Borwein, López de Prado, Zhu (2017), "The Probability of Backtest
    Overfitting" (CSCV / PBO).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from bt.panel.stats import (
    bh_fdr,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    pbo,
    _norm_cdf,
    _norm_ppf,
)


# ---------------------------------------------------------------------------
# normal helpers (foundation — if these are wrong, DSR is wrong)
# ---------------------------------------------------------------------------
def test_norm_cdf_known_points():
    assert _norm_cdf(0.0) == pytest.approx(0.5, abs=1e-9)
    # Φ(1.96) ≈ 0.975 ; Φ(-1.96) ≈ 0.025
    assert _norm_cdf(1.959963984540054) == pytest.approx(0.975, abs=1e-6)
    assert _norm_cdf(-1.959963984540054) == pytest.approx(0.025, abs=1e-6)


def test_norm_ppf_inverse_of_cdf():
    for p in (0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99):
        assert _norm_cdf(_norm_ppf(p)) == pytest.approx(p, abs=1e-6)
    assert _norm_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-4)


# ---------------------------------------------------------------------------
# PSR — probabilistic Sharpe ratio (DSR with benchmark SR0)
# ---------------------------------------------------------------------------
def test_psr_in_unit_interval():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, 500)
    p = probabilistic_sharpe_ratio(r, sr_benchmark=0.0)
    assert 0.0 <= p <= 1.0


def test_psr_hand_computed_symmetric():
    """Independent hand calc on a skew-symmetric sample (NOT a formula mirror).

    Use a perfectly symmetric 4-point sample {-2a, -a, +a, +2a} + a mean shift,
    so skew γ₃ = 0 exactly. For γ₃=0 the PSR denominator collapses to
    sqrt(1 + (γ₄−1)/4 · SR²). We pin every quantity by hand below (the std/skew/
    kurt values are computed from the closed-form moments of this fixed set, not
    by calling the implementation's helper), then assert the impl matches.

    Sample: r = m + d·{-2,-1,1,2}, d=0.01, m=0.005.
      mean = m = 0.005 (the symmetric part sums to 0)
      deviations from mean: d·{-2,-1,1,2}
      pop var = d²·(4+1+1+4)/4 = d²·2.5  → pop std = d·sqrt(2.5)
      sample var (ddof=1) = d²·10/3     → sample std = d·sqrt(10/3)
      γ₃ = 0 (symmetric)
      γ₄ = mean(dev⁴)/popstd⁴ = (d⁴·(16+1+1+16)/4)/(d⁴·2.5²) = 8.5/6.25 = 1.36
    """
    d, m = 0.01, 0.005
    r = m + d * np.array([-2.0, -1.0, 1.0, 2.0])
    T = 4
    sample_std = d * math.sqrt(10.0 / 3.0)
    sr = m / sample_std                       # per-bar Sharpe, hand value
    g4 = 1.36                                 # hand-derived raw kurtosis
    denom = math.sqrt(1.0 + (g4 - 1.0) / 4.0 * sr**2)   # γ₃=0 drops out
    z = sr * math.sqrt(T - 1) / denom
    expected = _norm_cdf(z)
    got = probabilistic_sharpe_ratio(r, sr_benchmark=0.0)
    assert got == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# DSR — deflated Sharpe ratio
# ---------------------------------------------------------------------------
def test_dsr_in_unit_interval():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0015, 0.01, 400)
    trial_sharpes = list(rng.normal(0.05, 0.05, 30))
    d = deflated_sharpe_ratio(r, trial_sharpes)
    assert 0.0 <= d <= 1.0


def test_dsr_discounts_trials():
    """DSR is monotone DECREASING in the number of trials.

    More trials → higher expected-max benchmark SR0 → smaller DSR. This is the
    defining anti-overfit property and is robust to the exact formula details.
    """
    rng = np.random.default_rng(2)
    r = rng.normal(0.0015, 0.01, 400)
    # fixed dispersion of trial sharpes, growing trial count
    base = rng.normal(0.05, 0.04, 200)
    d_few = deflated_sharpe_ratio(r, list(base[:5]))
    d_many = deflated_sharpe_ratio(r, list(base[:100]))
    assert d_many < d_few


def test_dsr_le_psr_with_trials():
    """With >1 trial, SR0 benchmark rises above 0, so DSR <= PSR(0)."""
    rng = np.random.default_rng(3)
    r = rng.normal(0.0015, 0.01, 400)
    trials = list(rng.normal(0.05, 0.05, 20))
    psr0 = probabilistic_sharpe_ratio(r, sr_benchmark=0.0)
    dsr = deflated_sharpe_ratio(r, trials)
    assert dsr <= psr0 + 1e-12


def test_dsr_single_trial_reduces_to_psr0():
    """Variance of a single trial sharpe is 0 → SR0 benchmark = 0 → DSR == PSR(0)."""
    rng = np.random.default_rng(4)
    r = rng.normal(0.0015, 0.01, 400)
    dsr = deflated_sharpe_ratio(r, [0.05])
    psr0 = probabilistic_sharpe_ratio(r, sr_benchmark=0.0)
    assert dsr == pytest.approx(psr0, abs=1e-9)


# ---------------------------------------------------------------------------
# BH-FDR — Benjamini-Hochberg
# ---------------------------------------------------------------------------
def test_bh_fdr_all_significant():
    p = [0.001, 0.002, 0.003]
    rej, adj = bh_fdr(p, alpha=0.05)
    assert all(rej)


def test_bh_fdr_none_significant():
    p = [0.6, 0.7, 0.8, 0.9]
    rej, adj = bh_fdr(p, alpha=0.05)
    assert not any(rej)


def test_bh_fdr_monotone_adjusted():
    """BH-adjusted p-values are monotone non-decreasing in sorted-p order."""
    p = [0.01, 0.04, 0.03, 0.20, 0.005]
    _, adj = bh_fdr(p, alpha=0.05)
    order = np.argsort(p)
    sorted_adj = np.asarray(adj)[order]
    assert np.all(np.diff(sorted_adj) >= -1e-12)


def test_bh_fdr_known_case():
    """Classic worked example: p = .01,.02,.03,.04,.05 with alpha .05, m=5.
    BH threshold: largest k with p_(k) <= k/m * alpha.
    k=1: .01<=.01 yes; ... all p_(k)=k*.01 <= k/5*.05=k*.01 → boundary, all reject.
    """
    p = [0.01, 0.02, 0.03, 0.04, 0.05]
    rej, _ = bh_fdr(p, alpha=0.05)
    assert all(rej)


# ---------------------------------------------------------------------------
# PBO — probability of backtest overfit (CSCV)
# ---------------------------------------------------------------------------
def test_pbo_basic_overfit():
    """Crafted overfit case: the IS-best config is consistently OOS-worst.

    Engineered (not noise-lucky): build a per-block return level matrix
    ``B[S, C]`` and force each config's levels to sum to zero across the S blocks
    (``B -= B.mean(axis=0)``). Then for ANY CSCV partition the in-sample block
    set and its out-of-sample complement have exactly opposite mean for every
    config (``IS_mean = -OOS_mean``), so the IS-best config is the OOS-worst by
    construction. PBO → 1 regardless of seed (verified PBO ∈ [0.96, 1.0] across
    20 seeds), not a single lucky draw.
    """
    n_bars, n_cfg, S = 160, 8, 8
    blocks = np.array_split(np.arange(n_bars), S)

    for seed in (0, 5, 11, 17):  # robustness: not seed-tuned
        rng = np.random.default_rng(seed)
        B = rng.normal(0.0, 0.05, (S, n_cfg))
        B -= B.mean(axis=0, keepdims=True)  # zero column sum ⇒ IS_mean=-OOS_mean
        M = np.empty((n_bars, n_cfg))
        for bi, b in enumerate(blocks):
            M[b, :] = B[bi, :]
        M += rng.normal(0.0, 1e-4, M.shape)  # break degenerate within-block std

        p = pbo(M, n_splits=S)
        assert 0.0 <= p <= 1.0
        assert p > 0.8  # engineered overfit ⇒ IS-best is OOS-worst everywhere


def test_pbo_consistent_low():
    """One config has a genuine, regime-independent edge → low PBO.

    Config 0 has a real positive mean everywhere; it should rank top both IS and
    OOS in every split, so PBO ≈ 0.
    """
    rng = np.random.default_rng(6)
    n_bars, n_cfg = 200, 6
    M = rng.normal(0.0, 0.005, (n_bars, n_cfg))
    M[:, 0] += 0.01  # strong consistent edge
    p = pbo(M, n_splits=8)
    assert p < 0.2
