"""P2 — wire loader → strategy → run_panel, plus the C4 causality detector.

``run_panel_strategy`` turns a :class:`Panel` + :class:`PanelStrategy` into a
``PortfolioResult`` dict via the Rust ``bt.bt_engine.run_panel`` binding.

``detect_lookahead`` is a future-perturbation dependency detector for raw-array
cheats the helper guard cannot see (C4, mechanism 2): it scrambles the tail rows
of the price/flow/fx fields AND the survivorship dimensions (``active_mask``,
``sector``) and asserts ``W[:k]`` is invariant. It tests the *defining property*
of look-ahead (does ``W[:k]`` depend on rows ``>= k``) across every non-calendar
field, not equity divergence.
"""

from __future__ import annotations

import numpy as np

from bt.bt_engine import run_panel
from bt.panel.loader import Panel
from bt.panel.strategy import PanelView, StrategyFn


def _coerce_strategy(strategy) -> StrategyFn:
    """Accept either a PanelStrategy (has ``.compute``) or a plain callable."""
    if hasattr(strategy, "compute"):
        return strategy.compute
    return strategy


def run_panel_strategy(panel: Panel, strategy, *, cfg: dict) -> dict:
    """Run ``strategy`` over ``panel`` through the Rust portfolio engine.

    Args:
        panel: aligned :class:`Panel` from :func:`bt.panel.loader.load_panel`.
        strategy: a :class:`PanelStrategy` (or callable ``PanelView -> WeightMatrix``).
        cfg: ``run_panel`` portfolio config dict (see P1 binding / spec §4-5).

    Returns:
        The ``run_panel`` result dict (``equity``, ``gross_exposure``, …).
    """
    pv = PanelView(panel)
    wm = _coerce_strategy(strategy)(pv)
    return _run_weights(panel, wm.W, cfg)


def _run_weights(panel: Panel, W: np.ndarray, cfg: dict) -> dict:
    """Execute a concrete weight matrix through ``run_panel``."""
    t, n = panel.close.shape
    decision_time = panel.timestamp.astype(np.int64)
    sessions = panel.session.astype(np.int64)
    active = panel.active_mask.astype(np.float64)
    return run_panel(
        panel.assets,
        np.ascontiguousarray(panel.close, dtype=np.float64),
        np.ascontiguousarray(panel.open, dtype=np.float64),
        np.ascontiguousarray(panel.funding, dtype=np.float64),
        np.ascontiguousarray(panel.fx, dtype=np.float64),
        np.ascontiguousarray(W, dtype=np.float64),
        active,
        sessions,
        decision_time,
        cfg,
    )


# Fields a cheat could read forward — perturbed in the tail by the dependency
# test. The discriminator is "forward-KNOWN published calendar" vs "future
# EVENT/observation":
#
#   * ``close/open/high/low/volume/funding`` — market prices/flows. ``fx`` is a
#     market PRICE too (the numeraire conversion rate, spec §3.4) exposed via
#     PanelView.fx, so an fx[t+1] reader is genuine look-ahead.
#   * ``active_mask`` — universe membership over time (spec §3.1: "Guards
#     survivorship"). Listing/delisting/data-gaps are future EVENTS, NOT a
#     forward-known calendar, so reading active_mask[t+1] (e.g. "long only names
#     still alive next bar") is exactly the survivorship leak the active-mask is
#     meant to prevent. It MUST be perturbed. The earlier exclusion claimed the
#     honest primitives would false-positive because they read active_mask — that
#     is provably wrong: CrossSectionalStrategy reads active_mask[ti] at the SAME
#     row ti it writes W[ti] (cross-sectional, same-row), so for ti<k it only
#     touches UNperturbed rows and W[:k] is invariant under a tail scramble
#     (verified by test_honest_strategy_delisting_not_flagged). Perturbed.
#   * ``sector`` — classification. If sectors are static it is metadata; if they
#     vary over time, sector[t+1] is a forward read (reclassification is an event,
#     not a calendar). sector_demean reads sector[ti] same-row, so perturbing the
#     tail does not false-positive the honest path. Perturbed (tail labels shuffled).
#
# EXCLUDED (genuinely forward-known): ``timestamp`` and ``session`` — a published
# trading calendar known in advance; reading session[t+1] is legitimate (you know
# the exchange hours ahead of time), so perturbing them WOULD false-positive the
# honest session-gated primitives. Left untouched.
_PERTURB_FIELDS = (
    "close", "open", "high", "low", "volume", "funding", "fx", "active_mask", "sector",
)


def _perturb_tail(panel: Panel, k: int, rng: np.random.Generator) -> Panel:
    """Return a copy of ``panel`` with rows ``>= k`` of the perturb fields
    scrambled (type-appropriate). Rows ``< k`` are byte-identical to the
    original."""
    from dataclasses import replace

    kwargs: dict = {}
    for name in _PERTURB_FIELDS:
        arr = getattr(panel, name).copy()
        tail = arr[k:]
        if tail.size == 0:
            kwargs[name] = arr
            continue
        if name == "funding":
            # additive scramble (funding can be 0/negative).
            arr[k:] = tail + rng.normal(0.0, 0.01, tail.shape)
        elif name == "active_mask":
            # bool — flip a random fraction of tail membership bits so a strategy
            # reading active_mask[t+1] sees a different future universe.
            flip = rng.random(tail.shape) < 0.5
            arr[k:] = tail ^ flip
        elif name == "sector":
            # object labels — permute the flattened tail so a sector[t+1] reader
            # sees relabelled future classifications.
            flat = tail.reshape(-1).copy()
            perm = rng.permutation(flat.size)
            arr[k:] = flat[perm].reshape(tail.shape)
        else:
            # multiplicative scramble keeps prices positive & finite.
            factor = rng.uniform(0.5, 1.5, tail.shape)
            arr[k:] = np.where(np.isnan(tail), tail, tail * factor)
        kwargs[name] = arr
    return replace(panel, **kwargs)


def detect_lookahead(panel_or_view, strategy, *, n_splits: int = 3, seed: int = 0) -> bool:
    """Future-perturbation dependency detector for look-ahead (C4, mechanism 2).

    The trailing-only helper guard cannot see *raw* forward indexing
    (``panel.close[t+1]``, ``panel.close[t:].max()``, ``active_mask[t+1]``).
    This detector catches such cheats by testing the **defining property** of
    look-ahead directly: does ``W[:k]`` depend on panel rows ``>= k``?

    For each split ``k``, scramble rows ``>= k`` of every non-calendar field —
    price/flow/fx PLUS the survivorship dimensions ``active_mask``/``sector``
    (so an ``active_mask[t+1]`` survivorship-foreknowledge cheat is caught too) —
    and recompute ``W``:

    - **Honest** strategy: ``W[t] = f(rows <= t)`` → ``W[:k]`` is bit-identical
      under any tail scramble → not flagged.
    - ``close[t+1]`` / ``max(close[t:])`` cheat: ``W[:k]`` reads the scrambled
      tail → it changes → flagged.

    Deterministic (`np.allclose`), threshold-free, fill-model-independent —
    it tests causal dependency, not profitability. A cheat profitable only by
    accident still trips it iff it actually reads the future, which is the
    correct definition. (The full equity-vs-OOS divergence harness is P3; this
    is the P2-scoped causality canary.)

    Guarantee boundary (coverage limit). This detector catches **PERVASIVE**
    cheats (every-row look-ahead) at the first split: a strategy whose every
    ``W[t]`` reads ahead diverges on ``W[:k]`` for the smallest ``k``. A
    **TARGETED** single-bar cheat — one that reads row ``r`` and writes row
    ``ti < r`` — is caught only if some split ``k`` lands in ``(ti, r]`` (i.e.
    ``ti < k <= r``, so that ``W[:k]`` includes the poisoned write ``ti`` *and*
    the perturbation scrambles the read ``r``). With sparse interior splits
    ``k ∈ {T·i/(n_splits+1)}`` (e.g. {15,30,45} for T=60), such targeted cheats
    can slip **anywhere, including the leading rows**: a row-20 cheat reading
    ``close[21]`` is missed because ``k=15`` excludes ``W[20]`` and ``k∈{30,45}``
    do not perturb ``close[21]`` (21 < 30).

    Coverage is **cheat-class-dependent, and denser splits do NOT fix the
    discrete case** (corrected — the earlier claim that ``k = 1..T-1`` catches
    every targeted cheat except ``ti = T-1`` was false). Single-perturbation-
    per-split applies exactly one multiplicative draw to the read row at the one
    split ``k = r``; that only *probabilistically* flips a **discrete** output
    (argmax / top-k / threshold), so coverage of targeted discrete cheats is
    seed-dependent and incomplete **at any split density** — empirically dense
    splits still miss ~10-17 genuine interior discrete leaks per random panel.
    A **continuous**-dependency cheat (``W ∝ close[t+1]/close[t]``) IS caught at
    ``k = r`` under dense splits — any nonzero dependence trips ``np.allclose``.
    This matters because the shipped primitives' selection step (CS top-k/bottom-k,
    Pairs z-threshold) is **discrete** — exactly the class this detector is
    weakest against. Only multiple perturbations per split would improve discrete
    coverage (still probabilistic). It is **not** a full causality proof; it is a
    defense-in-depth canary. P3's OOS/bias layer must not treat a pass here as
    proof of causality for arbitrary targeted cheats.

    Accepts a :class:`Panel` or a :class:`PanelView`.
    """
    panel = panel_or_view._panel if isinstance(panel_or_view, PanelView) else panel_or_view
    fn = _coerce_strategy(strategy)

    W_real = np.nan_to_num(np.asarray(fn(PanelView(panel)).W, dtype=np.float64))
    T = panel.close.shape[0]
    rng = np.random.default_rng(seed)

    splits = sorted({max(1, T * i // (n_splits + 1)) for i in range(1, n_splits + 1)})
    for k in splits:
        pert = _perturb_tail(panel, k, rng)
        W_p = np.nan_to_num(np.asarray(fn(PanelView(pert)).W, dtype=np.float64))
        if not np.allclose(W_real[:k], W_p[:k], atol=1e-9):
            return True
    return False
