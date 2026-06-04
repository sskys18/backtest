"""P3 — bias point-in-time gate tests (TDD, written first), spec §13 / C1.

Two composed layers:
  * Rust ingestion seam (P1.6, ``validate_bias`` in portfolio_engine.rs) —
    mechanical: default-forbidden, ``available_at[t] <= decision_time[t]``,
    per-t availability mandatory (no scalar / window-length mismatch), finite,
    shape. We test these flow through the REAL ``run_panel`` so the composition
    is exercised, not mocked.
  * Python higher-level gate (``bias.py``) — provenance/fold-locality/embargo +
    the window-constant smuggle the Rust seam cannot see (an all-identical
    ``available_at`` stamping "today's view" backward across a fold passes the
    `<=` check yet is exactly the §13 leak).
"""

from __future__ import annotations

import numpy as np
import pytest

from bt.panel.bias import (
    BiasArtifact,
    BiasRejected,
    BiasRunRegistry,
    BiasSourceManifest,
    fingerprint_values,
    validate_bias_artifact,
    apply_bias,
)
from bt.panel.loader import Panel
from bt.panel.walkforward import Fold


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _panel(T: int = 12, N: int = 2, step: int = 86_400_000) -> Panel:
    ts = (np.arange(T, dtype=np.int64) + 1) * step
    close = 100.0 + np.arange(T * N, dtype=np.float64).reshape(T, N)
    return Panel(
        assets=[f"A{i}" for i in range(N)],
        timestamp=ts,
        open=close.copy(),
        high=close.copy(),
        low=close.copy(),
        close=close,
        volume=np.ones((T, N)),
        funding=np.zeros((T, N)),
        fx=np.ones((T, N)),
        session=np.full((T, N), 2, dtype=np.uint8),
        sector=np.full((T, N), "x", dtype=object),
        active_mask=np.ones((T, N), dtype=bool),
        numeraire="USD",
        numeraire_is_unit_fx=True,
    )


def _fold(p: Panel) -> Fold:
    # train = first 6 bars, test = last 6 bars
    return Fold(
        train_idx=np.arange(0, 6),
        test_idx=np.arange(6, 12),
        scheme="rolling",
        index=0,
    )


def _good_manifest(p: Panel, f: Fold) -> BiasSourceManifest:
    # train-local: produced from train window only, generated at last train ts
    return BiasSourceManifest(
        run_id="fold0_train",
        train_start=int(p.timestamp[f.train_idx[0]]),
        train_end=int(p.timestamp[f.train_idx[-1]]),
        generated_at=int(p.timestamp[f.train_idx[-1]]),
    )


def _registry_with(manifest: BiasSourceManifest, *, fingerprint=None) -> BiasRunRegistry:
    """Fresh registry recording the manifest's run as ACTUAL fold-local provenance.

    Records the SAME window the manifest self-reports — used for the happy path
    and for tests that must reach a check downstream of registry resolution.
    """
    reg = BiasRunRegistry()
    reg.register(
        manifest.run_id,
        manifest.train_start,
        manifest.train_end,
        generated_at=manifest.generated_at,
        fingerprint=fingerprint,
    )
    return reg


def _per_t_available_at(p: Panel, f: Fold) -> np.ndarray:
    """A genuine point-in-time available_at: each test bar's bias is available at
    the prior bar's timestamp (decision time), train bars at their own ts."""
    T = p.timestamp.size
    av = p.timestamp.copy().astype(np.int64)
    # shift by one bar back so available_at[t] < decision_time[t]
    av[1:] = p.timestamp[:-1]
    av[0] = p.timestamp[0]
    return av


# ---------------------------------------------------------------------------
# Python-only checks (bias.py) — Rust seam cannot see these
# ---------------------------------------------------------------------------
def test_bias_untyped_rejected():
    """A raw ndarray (no BiasArtifact wrapper) is rejected at the Python gate."""
    p = _panel()
    f = _fold(p)
    raw = np.zeros((p.timestamp.size, len(p.assets)))
    with pytest.raises(BiasRejected):
        validate_bias_artifact(raw, panel=p, fold=f)  # type: ignore[arg-type]


def test_bias_default_forbidden_in_backtest():
    """Artifact with mode unset/None → rejected (default forbidden)."""
    p = _panel()
    f = _fold(p)
    art = BiasArtifact(
        bias_values=np.zeros((p.timestamp.size, len(p.assets))),
        available_at=_per_t_available_at(p, f),
        source=_good_manifest(p, f),
        mode=None,  # unset
    )
    with pytest.raises(BiasRejected):
        validate_bias_artifact(art, panel=p, fold=f)


def test_bias_window_constant_rejected():
    """All-identical available_at ('today's view' stamped backward) → rejected.

    This is THE smuggle the Rust seam misses: a single generation timestamp
    that is <= every decision_time passes the mechanical `<=` check, but is the
    §13 leak. The Python gate must reject a window-constant availability."""
    p = _panel()
    f = _fold(p)
    const_av = np.full(p.timestamp.size, int(p.timestamp[0]), dtype=np.int64)
    art = BiasArtifact(
        bias_values=np.full((p.timestamp.size, len(p.assets)), 0.1),
        available_at=const_av,
        source=_good_manifest(p, f),
        mode="backtest",
    )
    with pytest.raises(BiasRejected):
        validate_bias_artifact(art, panel=p, fold=f)


def test_bias_scalar_rejected():
    """available_at of wrong length (scalar/window-constant by length) rejected."""
    p = _panel()
    f = _fold(p)
    art = BiasArtifact(
        bias_values=np.zeros((p.timestamp.size, len(p.assets))),
        available_at=np.array([int(p.timestamp[0])], dtype=np.int64),  # length 1
        source=_good_manifest(p, f),
        mode="backtest",
    )
    with pytest.raises(BiasRejected):
        validate_bias_artifact(art, panel=p, fold=f)


def test_bias_missing_available_at_rejected():
    p = _panel()
    f = _fold(p)
    art = BiasArtifact(
        bias_values=np.zeros((p.timestamp.size, len(p.assets))),
        available_at=None,
        source=_good_manifest(p, f),
        mode="backtest",
    )
    with pytest.raises(BiasRejected):
        validate_bias_artifact(art, panel=p, fold=f)


def test_bias_future_available_at_rejected():
    """available_at[t] > decision_time[t] (panel timestamp) → rejected."""
    p = _panel()
    f = _fold(p)
    bad_av = p.timestamp.copy().astype(np.int64)
    bad_av[8] = int(p.timestamp[-1]) + 10  # a future availability
    art = BiasArtifact(
        bias_values=np.zeros((p.timestamp.size, len(p.assets))),
        available_at=bad_av,
        source=_good_manifest(p, f),
        mode="backtest",
    )
    with pytest.raises(BiasRejected):
        validate_bias_artifact(art, panel=p, fold=f)


def test_bias_non_fold_local_source_run_id_rejected():
    """A run REGISTERED with a non-fold-local (whole-sample) window → rejected.

    The run_id resolves (it is registered), so this exercises the REGISTERED-bounds
    fold-locality rejection path, distinct from the fabricated-id path."""
    p = _panel()
    f = _fold(p)
    whole_sample = BiasSourceManifest(
        run_id="whole",
        train_start=int(p.timestamp[0]),
        train_end=int(p.timestamp[-1]),  # spans test window → leak
        generated_at=int(p.timestamp[-1]),
    )
    reg = _registry_with(whole_sample)  # registered with its actual non-local window
    art = BiasArtifact(
        bias_values=np.zeros((p.timestamp.size, len(p.assets))),
        available_at=_per_t_available_at(p, f),
        source=whole_sample,
        mode="backtest",
    )
    with pytest.raises(BiasRejected, match="non-fold-local"):
        validate_bias_artifact(art, panel=p, fold=f, registry=reg)


# ---------------------------------------------------------------------------
# C1 mechanical-resolution gate (the §13 leak this fix closes)
# ---------------------------------------------------------------------------
def test_bias_fabricated_run_id_rejected():
    """THE exploit: a manifest with a run_id that was NEVER registered, carrying
    hindsight values stamped with genuine per-bar PIT available_at and self-reported
    fold-local bounds, must be REJECTED.

    Pre-fix this passed (the gate only shape-checked self-reported bounds); the
    mechanical registry resolution now kills it — an unresolved run_id cannot be
    accepted no matter how plausible its self-reported window or PIT stamps."""
    p = _panel()
    f = _fold(p)
    # genuine-looking PIT availability + fold-local SELF-REPORTED bounds ...
    fabricated = BiasSourceManifest(
        run_id="i_never_existed",
        train_start=int(p.timestamp[f.train_idx[0]]),
        train_end=int(p.timestamp[f.train_idx[-1]]),
        generated_at=int(p.timestamp[f.train_idx[-1]]),
    )
    # ... but the values are HINDSIGHT (a tilt nobody legitimately produced)
    art = BiasArtifact(
        bias_values=np.full((p.timestamp.size, len(p.assets)), 0.42),
        available_at=_per_t_available_at(p, f),
        source=fabricated,
        mode="backtest",
    )
    reg = BiasRunRegistry()  # EMPTY — the run was never recorded
    with pytest.raises(BiasRejected, match="unresolved/fabricated run_id"):
        validate_bias_artifact(art, panel=p, fold=f, registry=reg)


def test_bias_lying_bounds_rejected():
    """A run REGISTERED with a NON-fold-local actual window, but the artifact
    SELF-REPORTS a fold-local window → rejected (registered bounds win)."""
    p = _panel()
    f = _fold(p)
    # artifact lies: claims a fold-local window
    lying = BiasSourceManifest(
        run_id="liar",
        train_start=int(p.timestamp[f.train_idx[0]]),
        train_end=int(p.timestamp[f.train_idx[-1]]),  # fold-local SELF-REPORT
        generated_at=int(p.timestamp[f.train_idx[-1]]),
    )
    # but the REGISTRY records the run's ACTUAL window as whole-sample (sees test)
    reg = BiasRunRegistry()
    reg.register(
        "liar",
        int(p.timestamp[0]),
        int(p.timestamp[-1]),  # ACTUAL window spans the test segment → leak
        generated_at=int(p.timestamp[-1]),
    )
    art = BiasArtifact(
        bias_values=np.zeros((p.timestamp.size, len(p.assets))),
        available_at=_per_t_available_at(p, f),
        source=lying,
        mode="backtest",
    )
    with pytest.raises(BiasRejected, match="non-fold-local"):
        validate_bias_artifact(art, panel=p, fold=f, registry=reg)


def test_bias_forward_mode_skips_registry_resolution():
    """mode='forward' is injected live going forward and never touches a historical
    fold (§13 rule 2), so registry resolution / fold-locality do NOT apply: an
    UNREGISTERED run_id with valid per-t PIT availability is ACCEPTED.

    Pins the carve-out — proves the registry gate is scoped to backtest only and
    documents the embargo/fold-locality skip for forward as intended."""
    p = _panel()
    f = _fold(p)
    unregistered = BiasSourceManifest(
        run_id="live_forward_thesis",  # never registered
        train_start=int(p.timestamp[0]),
        train_end=int(p.timestamp[-1]),  # would be non-local in a backtest
        generated_at=int(p.timestamp[-1]),
    )
    art = BiasArtifact(
        bias_values=np.full((p.timestamp.size, len(p.assets)), 0.05),
        available_at=_per_t_available_at(p, f),
        source=unregistered,
        mode="forward",
    )
    # empty registry + large embargo: backtest would reject; forward accepts
    out = validate_bias_artifact(
        art, panel=p, fold=f, embargo_bars=4, registry=BiasRunRegistry()
    )
    assert out is art


def test_bias_fingerprint_tamper_rejected():
    """Registered fingerprint A, artifact self-reports fingerprint A but carries
    values hashing to B → rejected (tamper check)."""
    p = _panel()
    f = _fold(p)
    mani = _good_manifest(p, f)
    genuine = np.full((p.timestamp.size, len(p.assets)), 0.05)
    fp_a = fingerprint_values(genuine)
    reg = BiasRunRegistry()
    reg.register(
        mani.run_id, mani.train_start, mani.train_end,
        generated_at=mani.generated_at, fingerprint=fp_a,
    )
    tampered_mani = BiasSourceManifest(
        run_id=mani.run_id,
        train_start=mani.train_start,
        train_end=mani.train_end,
        generated_at=mani.generated_at,
        values_fingerprint=fp_a,  # claims the registered fingerprint ...
    )
    art = BiasArtifact(
        bias_values=np.full((p.timestamp.size, len(p.assets)), 0.99),  # ... but B
        available_at=_per_t_available_at(p, f),
        source=tampered_mani,
        mode="backtest",
    )
    with pytest.raises(BiasRejected, match="fingerprint mismatch"):
        validate_bias_artifact(art, panel=p, fold=f, registry=reg)


def test_bias_registered_fold_local_accepted():
    """A run registered with a GENUINELY fold-local window → accepted, and a REAL
    run_panel(bias=...) applies the tilt (no mock)."""
    p = _panel()
    f = _fold(p)
    mani = _good_manifest(p, f)
    bv = np.full((p.timestamp.size, len(p.assets)), 0.05)
    reg = BiasRunRegistry()
    reg.register(
        mani.run_id,
        mani.train_start,
        mani.train_end,
        generated_at=mani.generated_at,
        fingerprint=fingerprint_values(bv),
    )
    mani = BiasSourceManifest(
        run_id=mani.run_id,
        train_start=mani.train_start,
        train_end=mani.train_end,
        generated_at=mani.generated_at,
        values_fingerprint=fingerprint_values(bv),
    )
    art = BiasArtifact(
        bias_values=bv,
        available_at=_per_t_available_at(p, f),
        source=mani,
        mode="backtest",
    )
    # gate passes (no raise) and the real Rust seam applies the tilt
    validate_bias_artifact(art, panel=p, fold=f, embargo_bars=0, registry=reg)
    cfg = {
        "initial_collateral": 10_000.0,
        "fee_rate": 0.0,
        "slippage_bps": 0.0,
        "im_rate": 0.1,
        "mm_rate": 0.05,
        "leverage_cap": 2.0,
        "max_net": 2.0,
        "max_per_asset": 1.0,
        "rebalance_freq": 1,
        "numeraire_is_unit_fx": True,
    }
    W = np.zeros((p.timestamp.size, len(p.assets)))
    W[:, 0] = 0.3
    res = apply_bias(p, W, art, run_cfg=cfg, fold=f, embargo_bars=0, registry=reg)
    assert "equity" in res
    assert len(res["equity"]) == p.timestamp.size


def test_bias_fails_purge_embargo_rejected():
    """A test-bar bias whose availability sits inside the purge gap after train
    end (no embargo respected) → rejected when embargo_bars is enforced.

    Registers the run with a genuinely fold-local window so resolution PASSES and
    the gate actually reaches the embargo check (without registration it would
    short-circuit at fold-locality and this test would no longer have teeth)."""
    p = _panel()
    f = _fold(p)
    mani = _good_manifest(p, f)
    reg = _registry_with(mani)
    av = _per_t_available_at(p, f)
    art = BiasArtifact(
        bias_values=np.zeros((p.timestamp.size, len(p.assets))),
        available_at=av,
        source=mani,
        mode="backtest",
    )
    # require a large embargo so the bias availability for the first test bars
    # falls inside the embargo gap → reject
    with pytest.raises(BiasRejected, match="embargo"):
        validate_bias_artifact(art, panel=p, fold=f, embargo_bars=4, registry=reg)


# ---------------------------------------------------------------------------
# valid path — composes Python gate + REAL Rust run_panel
# ---------------------------------------------------------------------------
def test_bias_valid_fold_local_pit_accepted():
    """All gates pass → validate returns the matrix; a real run_panel(bias=...)
    call with mode='backtest' runs and applies the tilt."""
    p = _panel()
    f = _fold(p)
    mani = _good_manifest(p, f)
    reg = _registry_with(mani)
    av = _per_t_available_at(p, f)
    art = BiasArtifact(
        bias_values=np.full((p.timestamp.size, len(p.assets)), 0.05),
        available_at=av,
        source=mani,
        mode="backtest",
    )
    # passes the Python gate (returns validated artifact, no raise)
    validate_bias_artifact(art, panel=p, fold=f, embargo_bars=0, registry=reg)

    # composes with the real Rust seam: apply_bias runs run_panel(bias=..., mode)
    cfg = {
        "initial_collateral": 10_000.0,
        "fee_rate": 0.0,
        "slippage_bps": 0.0,
        "im_rate": 0.1,
        "mm_rate": 0.05,
        "leverage_cap": 2.0,
        "max_net": 2.0,
        "max_per_asset": 1.0,
        "rebalance_freq": 1,
        "numeraire_is_unit_fx": True,
    }
    W = np.zeros((p.timestamp.size, len(p.assets)))
    W[:, 0] = 0.3
    res = apply_bias(p, W, art, run_cfg=cfg, fold=f, embargo_bars=0, registry=reg)
    assert "equity" in res
    assert len(res["equity"]) == p.timestamp.size


def test_bias_future_rejected_by_rust_seam():
    """Even if a caller bypasses validate_bias_artifact, the Rust seam rejects a
    future available_at at run_panel ingestion (mechanical boundary)."""
    from bt.bt_engine import run_panel

    p = _panel()
    T, N = p.timestamp.size, len(p.assets)
    bad_av = p.timestamp.copy().astype(np.int64)
    bad_av[5] = int(p.timestamp[-1]) + 1000
    cfg = {
        "initial_collateral": 10_000.0,
        "fee_rate": 0.0,
        "slippage_bps": 0.0,
        "im_rate": 0.1,
        "mm_rate": 0.05,
        "leverage_cap": 2.0,
        "max_net": 2.0,
        "max_per_asset": 1.0,
        "rebalance_freq": 1,
        "numeraire_is_unit_fx": True,
    }
    with pytest.raises(Exception):
        run_panel(
            p.assets,
            np.ascontiguousarray(p.close),
            np.ascontiguousarray(p.open),
            np.ascontiguousarray(p.funding),
            np.ascontiguousarray(p.fx),
            np.zeros((T, N)),
            p.active_mask.astype(np.float64),
            p.session.astype(np.int64),
            p.timestamp.astype(np.int64),
            cfg,
            np.full((T, N), 0.05),
            bad_av,
            "backtest",
        )
