"""P2 — C4 canary battery (finding #3, critical).

A perfect-foresight scorer must be caught for EACH cheat form. Two catch
mechanisms (defense-in-depth, NOT a sandbox — see strategy.py docstring):

  1. Helper-routed cheats (centered window, full-sample z-score without
     `as_of`) → the trailing-only helper raises LookaheadError. Caught
     mechanically.
  2. Raw-array cheats (`close[t+1]`, `max(close[t:])`, `fx[t+1]`,
     `active_mask[t+1]`, `sector[t+1]`) → the helper guard cannot see raw
     indexing. These are caught by the future-perturbation dependency detector
     (`detect_lookahead`): scramble the tail rows of the price/flow/fx fields
     AND the survivorship dimensions (active_mask/sector) and assert `W[:k]` is
     invariant. An honest strategy's early weights depend only on rows `<=t`
     so they are bit-identical under any tail scramble; a look-ahead cheat's
     early weights move. A cheat the detector lets through silently is a
     failure. (Coverage limit: only the leading rows are audited — see the
     detect_lookahead docstring.)

`bt.bt_engine.run_panel` is the execution engine the detector runs against.
"""

from __future__ import annotations

import numpy as np
import pytest

from bt.panel.loader import Panel
from bt.panel.run import detect_lookahead
from bt.panel.strategy import LookaheadError, PanelView, WeightMatrix


def _panel(t: int, n: int) -> Panel:
    rng = np.random.default_rng(7)
    # Random-walk closes: no real trend → an honest momentum strategy has ~0
    # edge, a perfect-foresight cheat has huge edge. Maximizes divergence.
    steps = rng.normal(0, 1.0, (t, n))
    close = 100.0 + np.cumsum(steps, axis=0)
    return Panel(
        assets=[f"A{j}" for j in range(n)],
        timestamp=np.arange(t, dtype=np.int64),
        open=close.copy(),
        high=close.copy(),
        low=close.copy(),
        close=close,
        volume=np.ones((t, n)),
        funding=np.zeros((t, n)),
        fx=np.ones((t, n)),
        session=np.full((t, n), 2, dtype=np.uint8),
        sector=np.full((t, n), "X", dtype=object),
        active_mask=np.ones((t, n), dtype=bool),
        numeraire="USD",
        numeraire_is_unit_fx=True,
    )


# ---- Mechanism 1: helper-routed cheats raise -----------------------------

def test_canary_centered_window():
    """Centered rolling window (peeks forward) → helper raises."""
    pv = PanelView(_panel(20, 3))
    with pytest.raises(LookaheadError):
        pv.rolling(pv.close, window=5, op="mean", center=True)


def test_canary_full_sample_zscore():
    """Full-sample z-score without `as_of` (uses whole column incl. future)."""
    pv = PanelView(_panel(20, 3))
    with pytest.raises(LookaheadError):
        pv.zscore(pv.close, as_of=None)


# ---- Mechanism 2: raw-array cheats trip the divergence detector -----------

def _honest_momentum(pv: PanelView) -> WeightMatrix:
    """Trailing momentum: long top asset by past return. No look-ahead."""
    c = pv.close
    t, n = c.shape
    W = np.zeros((t, n))
    for ti in range(1, t):
        ret = c[ti] / c[ti - 1] - 1.0
        W[ti, ret.argmax()] = 1.0
    return WeightMatrix(W, assets=pv.assets)


def _cheat_future_shift(pv: PanelView) -> WeightMatrix:
    """close[t+1] cheat: long whatever rises NEXT bar (raw indexing)."""
    c = pv.close
    t, n = c.shape
    W = np.zeros((t, n))
    for ti in range(t - 1):
        nxt = c[ti + 1] / c[ti] - 1.0
        W[ti, nxt.argmax()] = 1.0  # peeks at t+1
    return WeightMatrix(W, assets=pv.assets)


def _cheat_future_max(pv: PanelView) -> WeightMatrix:
    """max(close[t:]) cheat: long asset that peaks furthest ahead."""
    c = pv.close
    t, n = c.shape
    W = np.zeros((t, n))
    for ti in range(t - 1):
        future_max = c[ti:].max(axis=0)  # peeks at all future rows
        upside = future_max / c[ti] - 1.0
        W[ti, upside.argmax()] = 1.0
    return WeightMatrix(W, assets=pv.assets)


def _panel_varying_fx(t: int, n: int) -> Panel:
    """Like ``_panel`` but with a time-varying fx_rate column per asset.

    fx is a market PRICE (numeraire conversion, spec §3.4), so reading fx[t+1]
    is a genuine look-ahead the detector must catch.
    """
    rng = np.random.default_rng(11)
    p = _panel(t, n)
    fx = 1.0 + np.cumsum(rng.normal(0, 0.05, (t, n)), axis=0)
    from dataclasses import replace

    return replace(p, fx=fx)


def _cheat_future_fx(pv: PanelView) -> WeightMatrix:
    """fx[t+1] cheat: long whatever currency appreciates NEXT bar (raw fx index)."""
    fx = pv.fx
    t, n = fx.shape
    W = np.zeros((t, n))
    for ti in range(t - 1):
        appr = fx[ti + 1] / fx[ti] - 1.0  # peeks at fx[t+1]
        W[ti, appr.argmax()] = 1.0
    return WeightMatrix(W, assets=pv.assets)


def test_canary_future_fx():
    """fx is a market price; an fx[t+1] reader must be flagged (fx in perturb set)."""
    pv = PanelView(_panel_varying_fx(60, 4))
    flagged = detect_lookahead(pv, _cheat_future_fx)
    assert flagged is True


def _panel_tail_delisting(t: int, n: int) -> Panel:
    """Like ``_panel`` but asset ``n-1`` delists in the tail: active_mask False and
    close NaN for rows ``>= 40``. Models a survivorship event the engine guards."""
    from dataclasses import replace

    p = _panel(t, n)
    active = p.active_mask.copy()
    close = p.close.copy()
    active[40:, n - 1] = False
    close[40:, n - 1] = np.nan
    return replace(p, active_mask=active, close=close)


def _cheat_future_active(pv: PanelView) -> WeightMatrix:
    """active_mask[t+1] cheat: long only names that are still ALIVE next bar
    (survivorship foreknowledge). Reads the future universe — a genuine leak."""
    am = pv.active_mask
    t, n = am.shape
    W = np.zeros((t, n))
    for ti in range(t - 1):
        alive_next = am[ti + 1].astype(np.float64)  # peeks at active_mask[t+1]
        s = alive_next.sum()
        if s > 0:
            W[ti] = alive_next / s
    return WeightMatrix(W, assets=pv.assets)


def test_canary_future_active_mask():
    """active_mask is the survivorship guard; an active_mask[t+1] reader must be
    flagged (active_mask now in the perturb set)."""
    pv = PanelView(_panel_tail_delisting(60, 4))
    flagged = detect_lookahead(pv, _cheat_future_active)
    assert flagged is True


def test_honest_strategy_delisting_not_flagged():
    """Discriminator: the HONEST CS strategy reads active_mask[ti] same-row, so a
    tail active_mask scramble must NOT false-positive it (proves the perturb-set
    addition is sound, not just that the cheat trips)."""
    from bt.panel.primitives import CrossSectionalStrategy

    panel = _panel_tail_delisting(60, 4)
    cs = CrossSectionalStrategy(
        lookback=10, top_k=1, bottom_k=1, leverage_cap=1.0, weighting="equal"
    )
    assert detect_lookahead(panel, cs) is False


def _panel_tail_resector(t: int, n: int) -> Panel:
    """Like ``_panel`` but asset 0's sector switches to 'Y' in the tail (rows>=40):
    a time-varying classification a sector[t+1] reader could front-run."""
    from dataclasses import replace

    p = _panel(t, n)
    sector = p.sector.copy()
    sector[40:, 0] = "Y"
    return replace(p, sector=sector)


def _cheat_future_sector(pv: PanelView) -> WeightMatrix:
    """sector[t+1] cheat: long whatever is sector 'Y' NEXT bar (raw sector index)."""
    sec = pv.sector
    t, n = sec.shape
    W = np.zeros((t, n))
    for ti in range(t - 1):
        target = np.array([sec[ti + 1, j] == "Y" for j in range(n)], dtype=np.float64)
        s = target.sum()
        if s > 0:
            W[ti] = target / s
    return WeightMatrix(W, assets=pv.assets)


def test_canary_future_sector():
    """sector can be time-varying; a sector[t+1] reader must be flagged
    (sector now in the perturb set)."""
    pv = PanelView(_panel_tail_resector(60, 4))
    flagged = detect_lookahead(pv, _cheat_future_sector)
    assert flagged is True


def test_honest_sector_neutral_resector_not_flagged():
    """Symmetric no-false-positive proof for the sector half: the honest
    sector-neutral CS strategy reads sector[ti] same-row, so a tail sector
    permutation must NOT false-positive it."""
    from bt.panel.primitives import CrossSectionalStrategy

    panel = _panel_tail_resector(60, 4)
    cs = CrossSectionalStrategy(
        lookback=10, top_k=1, bottom_k=1, leverage_cap=1.0,
        weighting="equal", sector_neutral=True,
    )
    assert detect_lookahead(panel, cs) is False


def test_canary_future_shift():
    pv = PanelView(_panel(60, 4))
    flagged = detect_lookahead(pv, _cheat_future_shift)
    assert flagged is True


def test_canary_future_max():
    pv = PanelView(_panel(60, 4))
    flagged = detect_lookahead(pv, _cheat_future_max)
    assert flagged is True


def test_honest_strategy_not_flagged():
    """Detector must NOT flag an honest trailing strategy (no false positive)."""
    pv = PanelView(_panel(60, 4))
    flagged = detect_lookahead(pv, _honest_momentum)
    assert flagged is False


def _cheat_targeted_discrete(row: int):
    """A TARGETED single-bar DISCRETE cheat: only row ``row`` reads ``close[row+1]``
    and sets an argmax (top-1) weight. Every other row is honest (zero). This is
    the hardest class for the perturbation detector — a single discrete flip at one
    interior row — and the case FU4 (multi-perturbation) targets."""

    def _fn(pv: PanelView) -> WeightMatrix:
        c = pv.close
        t, n = c.shape
        W = np.zeros((t, n))
        nxt = c[row + 1] / c[row] - 1.0  # peeks at close[row+1] at exactly one row
        W[row, nxt.argmax()] = 1.0
        return WeightMatrix(W, assets=pv.assets)

    return _fn


def test_canary_targeted_discrete_caught_multi_perturbation():
    """FU4: a targeted single-bar discrete argmax cheat reading ``close[r+1]`` is
    caught with high reliability across seeds under multi-perturbation.

    Split-placement constraint: a row-``r`` cheat is auditable only at the split
    ``k = r+1`` (k must include the poisoned write ``W[r]`` *and* perturb the read
    ``close[r+1]``). For T=60, default splits are {15,30,45}, so we pick ``r=29``
    (read ``close[30]``, audited at k=30). ``n_perturb`` does not change which
    splits exist; it gives each auditing split multiple independent chances to flip
    the discrete argmax, raising catch reliability from ~80% (single draw) to
    near-certain.

    Measured: 30/30 seeds caught at the default n_perturb=8 (vs 24/30 at
    n_perturb=1). The detector is deterministic per seed, so this count is
    reproducible. We assert a margin below the measured rate (>=27/30) to stay
    honest about the probabilistic — not guaranteed — discrete bound.
    """
    panel = _panel(60, 4)
    assert (29 + 1) in {15, 30, 45}, "test row must be split-aligned (k=r+1)"
    cheat = _cheat_targeted_discrete(29)
    seeds = range(30)
    caught = sum(detect_lookahead(panel, cheat, seed=s) for s in seeds)
    assert caught >= 27, f"targeted discrete cheat caught only {caught}/30 seeds"


def test_targeted_discrete_single_perturbation_weaker():
    """Control: the SAME targeted discrete cheat is caught LESS reliably with a
    single perturbation per split — this is the documented pre-FU4 weakness the
    multi-perturbation fix addresses. Asserts the fix strictly helps (multi >=
    single, and single leaves a measurable gap)."""
    panel = _panel(60, 4)
    cheat = _cheat_targeted_discrete(29)
    seeds = range(30)
    single = sum(detect_lookahead(panel, cheat, n_perturb=1, seed=s) for s in seeds)
    multi = sum(detect_lookahead(panel, cheat, n_perturb=8, seed=s) for s in seeds)
    assert single < 30, f"single-perturbation expected to miss some seeds, got {single}/30"
    assert multi >= single, "multi-perturbation must not catch fewer than single"


def test_targeted_discrete_no_false_positive_multi_perturbation():
    """The honest momentum strategy must stay un-flagged at the FU4 default
    n_perturb=8 across many seeds — more draws must not manufacture a false
    positive (honest weights read rows <= t, invariant under any tail scramble)."""
    panel = _panel(60, 4)
    fp = sum(detect_lookahead(panel, _honest_momentum, n_perturb=8, seed=s) for s in range(30))
    assert fp == 0, f"honest strategy false-positived in {fp}/30 seeds"


def test_shipped_primitives_not_flagged():
    """The actual P2 primitives must not trip their own causality canary."""
    from bt.panel.primitives import CrossSectionalStrategy, PairsStrategy

    panel = _panel(60, 4)
    cs = CrossSectionalStrategy(
        lookback=10, top_k=1, bottom_k=1, leverage_cap=1.0, weighting="equal"
    )
    pairs = PairsStrategy(leg_long=0, leg_short=1, lookback=10, entry_z=1.0, gross=1.0)
    assert detect_lookahead(panel, cs) is False
    assert detect_lookahead(panel, pairs) is False
