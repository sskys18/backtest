"""P3 — walk-forward / OOS harness tests (TDD, written first).

Spec §7: rolling/anchored folds by date, purge+embargo (horizon-scaled),
per-fold tune-on-train / eval-on-test, aggregate OOS only.
"""

from __future__ import annotations

import numpy as np
import pytest

from bt.panel.loader import Panel
from bt.panel.walkforward import (
    Fold,
    _score_segment,
    _slice_panel,
    make_folds,
    walk_forward,
)


# ---------------------------------------------------------------------------
# synthetic panel helper
# ---------------------------------------------------------------------------
def _panel(T: int = 60, N: int = 2, step_ms: int = 86_400_000) -> Panel:
    rng = np.random.default_rng(0)
    ts = (np.arange(T, dtype=np.int64) * step_ms)
    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, (T, N)), axis=0)
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


# ---------------------------------------------------------------------------
# split mechanics
# ---------------------------------------------------------------------------
def test_make_folds_rolling_shapes():
    p = _panel(T=60)
    folds = make_folds(p.timestamp, scheme="rolling", n_folds=3,
                       embargo_bars=0, purge_bars=0)
    assert len(folds) == 3
    for f in folds:
        assert isinstance(f, Fold)
        assert f.train_idx.size > 0 and f.test_idx.size > 0


def test_make_folds_anchored_train_grows():
    p = _panel(T=80)
    folds = make_folds(p.timestamp, scheme="anchored", n_folds=4,
                       embargo_bars=0, purge_bars=0)
    train_sizes = [f.train_idx.size for f in folds]
    # anchored: train window starts at 0 and grows each fold
    assert all(f.train_idx[0] == 0 for f in folds)
    assert train_sizes == sorted(train_sizes)
    assert train_sizes[-1] > train_sizes[0]


def test_rolling_is_fixed_length_not_anchored():
    """Rolling train size stays ~constant (sliding window) while anchored grows.

    Pins the distinction so the two schemes can never silently collapse into the
    same expanding window (de Prado: rolling drops old bars, anchored keeps them).
    """
    p = _panel(T=80)
    rolling = make_folds(p.timestamp, scheme="rolling", n_folds=4,
                         embargo_bars=0, purge_bars=0)
    anchored = make_folds(p.timestamp, scheme="anchored", n_folds=4,
                          embargo_bars=0, purge_bars=0)
    roll_sizes = [f.train_idx.size for f in rolling]
    anch_sizes = [f.train_idx.size for f in anchored]
    # rolling: every fold has the same train length (fixed window)
    assert len(set(roll_sizes)) == 1
    # anchored: train grows strictly
    assert anch_sizes[-1] > anch_sizes[0]
    # later rolling folds start AFTER index 0 (old bars dropped); anchored at 0
    assert rolling[-1].train_idx[0] > 0
    assert anchored[-1].train_idx[0] == 0


def test_purge_embargo_no_overlap():
    """After purge+embargo, the combined (purge+embargo)-bar gap before the test
    block contains no train index, and train/test never overlap.

    In a forward walk-forward train always precedes the test block, so the gap
    that kills overlapping-horizon leakage is the band immediately *before* the
    first test bar — `[lo-(purge+embargo), lo)`. This asserts that whole band is
    empty of train rows (the embargo term has teeth: dropping it would shrink the
    band and let the assertion fail)."""
    p = _panel(T=90)
    purge, embargo = 3, 4
    folds = make_folds(p.timestamp, scheme="rolling", n_folds=3,
                       embargo_bars=embargo, purge_bars=purge)
    for f in folds:
        tr = set(f.train_idx.tolist())
        te = set(f.test_idx.tolist())
        assert tr.isdisjoint(te)
        lo = int(f.test_idx.min())
        # combined purge+embargo gap before the test block: no train index inside
        for g in range(lo - (purge + embargo), lo):
            assert g not in tr
        # the last train bar must sit at least (purge+embargo) bars before test
        if tr:
            assert f.train_idx.max() < lo - (purge + embargo)


def test_embargo_scales_with_holding_period():
    """embargo_bars derived from holding period × bar size strictly grows the
    train→test gap and strictly removes train rows adjacent to the test block."""
    p = _panel(T=90)
    f_small = make_folds(p.timestamp, scheme="rolling", n_folds=2,
                         embargo_bars=1, purge_bars=0)
    f_big = make_folds(p.timestamp, scheme="rolling", n_folds=2,
                       embargo_bars=10, purge_bars=0)

    def _gap(f):
        # bars between the last train row and the first test bar
        return int(f.test_idx.min()) - int(f.train_idx.max()) - 1

    # bigger embargo widens the train→test gap (the spec-mandated holding-period
    # scaling) and therefore strictly removes more adjacent train rows.
    assert _gap(f_big[0]) > _gap(f_small[0])
    assert f_big[0].train_idx.size < f_small[0].train_idx.size


def test_rolling_empty_train_window_raises():
    """When purge+embargo swallows the entire first train span (roll_len==0), the
    rolling scheme must raise rather than silently fall through to anchored."""
    p = _panel(T=12)
    # n_folds=3 → 4 blocks of ~3; first test block starts at index ~3, a gap of
    # purge+embargo >= 3 leaves zero usable train bars → roll_len == 0.
    with pytest.raises(ValueError, match="empty fixed train window"):
        make_folds(p.timestamp, scheme="rolling", n_folds=3,
                   embargo_bars=2, purge_bars=2)


# ---------------------------------------------------------------------------
# fold loop / OOS aggregation
# ---------------------------------------------------------------------------
def _const_long_strategy(params):
    """Factory: returns a PanelStrategy callable that longs asset 0 at `w`."""
    w = params.get("w", 0.5)

    def compute(pv):
        from bt.panel.strategy import WeightMatrix
        T, N = pv.close.shape
        W = np.zeros((T, N))
        W[:, 0] = w
        return WeightMatrix(W, list(pv.assets))

    return compute


def test_oos_only_aggregation():
    """Headline metrics come ONLY from concatenated OOS test segments; in-sample
    metrics never leak into the aggregate."""
    p = _panel(T=60)
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
    res = walk_forward(
        p,
        strategy_factory=_const_long_strategy,
        search_space={"w": {"min": 0.1, "max": 0.9}},
        run_cfg=cfg,
        scheme="rolling",
        n_folds=3,
        embargo_bars=1,
        purge_bars=1,
        budget=3,
        periods_per_year=252.0,
        session_aware=False,
    )
    assert "oos_sharpe" in res
    assert "folds" in res and len(res["folds"]) == 3
    # each fold reports a frozen best param and an OOS metric block
    for fr in res["folds"]:
        assert "best_params" in fr
        assert "oos_metrics" in fr
        assert "sharpe" in fr["oos_metrics"]
    # the headline OOS sharpe distribution has one entry per fold
    assert len(res["oos_sharpe"]) == 3

    # --- teeth: recompute the headline OOS sharpe INDEPENDENTLY and prove it is
    # sourced from the test (OOS) segment, never from the in-sample train metric.
    # The frozen eval is deterministic, so re-running each fold's frozen best
    # params on the test slice must reproduce res['oos_sharpe'] exactly. If the
    # implementation ever leaked train_sharpe into the headline, the recomputed
    # OOS value would diverge and these assertions would fail.
    folds = make_folds(p.timestamp, scheme="rolling", n_folds=3,
                       embargo_bars=1, purge_bars=1)
    diverged = False
    for i, f in enumerate(folds):
        fr = res["folds"][i]
        test_panel = _slice_panel(p, f.test_idx)
        recomputed = _score_segment(
            test_panel,
            _const_long_strategy(fr["best_params"]),
            cfg,
            periods_per_year=252.0,
            session_aware=False,
        )
        # headline == independently recomputed OOS-segment sharpe (the teeth):
        assert np.isclose(res["oos_sharpe"][i], recomputed["sharpe"])
        assert np.isclose(fr["oos_metrics"]["sharpe"], recomputed["sharpe"])
        # and the headline is NOT the train metric on at least one fold, so the
        # equality above is non-vacuous (train and OOS provably diverge here).
        if not np.isclose(fr["train_sharpe"], fr["oos_metrics"]["sharpe"]):
            diverged = True
    assert diverged, (
        "train and OOS sharpe never diverge on this panel — the OOS-vs-train "
        "comparison would be vacuous; pick a scenario where they differ"
    )


def _long_one_asset_strategy(params):
    """Factory whose param changes which asset it longs (shape-differing).

    Distinct ``asset`` values produce columns with genuinely DIFFERENT OOS
    return SHAPES (not just scale), so a CSCV/PBO performance matrix built from
    them is non-degenerate (per-column Sharpe cancels pure scale differences).
    """
    a = int(params.get("asset", 0))

    def compute(pv):
        from bt.panel.strategy import WeightMatrix
        T, N = pv.close.shape
        W = np.zeros((T, N))
        W[:, a % N] = 0.5
        return WeightMatrix(W, list(pv.assets))

    return compute


_WF_CFG = {
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


def test_walk_forward_emits_dsr_pbo_inputs():
    """FU1 — walk_forward returns the per-bar OOS series the stats fns need.

    Three new keys feed stats.dsr / stats.pbo: ``oos_returns`` (per-fold per-bar
    returns CONCATENATED — never equity-stitched), ``trial_sharpes`` (the search
    cardinality, de-annualized), ``oos_perf_matrix`` (``[T_oos, C]``, one column
    per distinct config re-run on the OOS segments).
    """
    p = _panel(T=80, N=4)
    res = walk_forward(
        p,
        strategy_factory=_long_one_asset_strategy,
        search_space={"asset": {"values": [0, 1, 2, 3]}},
        run_cfg=_WF_CFG,
        scheme="rolling",
        n_folds=3,
        embargo_bars=1,
        purge_bars=1,
        budget=4,
        periods_per_year=252.0,
        session_aware=False,
    )

    assert "oos_returns" in res and "trial_sharpes" in res and "oos_perf_matrix" in res

    # oos_returns is the CONCATENATION of per-fold per-bar returns of the frozen
    # candidate — recompute independently (no equity stitching across folds).
    folds = make_folds(p.timestamp, scheme="rolling", n_folds=3,
                       embargo_bars=1, purge_bars=1)
    expected_ret = []
    for i, f in enumerate(folds):
        fr = res["folds"][i]
        tp = _slice_panel(p, f.test_idx)
        fn = _long_one_asset_strategy(fr["best_params"])
        from bt.panel.run import _run_weights
        from bt.panel.strategy import PanelView
        out = _run_weights(tp, fn(PanelView(tp)).W, _WF_CFG)
        eq = np.asarray(out["equity"], dtype=np.float64)
        expected_ret.append(np.diff(eq) / eq[:-1])
    expected_ret = np.concatenate(expected_ret)
    got = np.asarray(res["oos_returns"], dtype=np.float64)
    assert got.shape == expected_ret.shape
    assert np.allclose(got, expected_ret)

    # perf matrix: T_oos rows, C distinct-config columns (here 4 assets)
    M = np.asarray(res["oos_perf_matrix"], dtype=np.float64)
    assert M.shape[0] == expected_ret.shape[0]
    assert M.shape[1] >= 2          # >=2 distinct configs were searched
    # columns are genuinely distinct (shape-differing), not duplicates
    assert not np.allclose(M[:, 0], M[:, 1])

    # trial sharpes are the SEARCH cardinality, not folds x budget
    assert len(res["trial_sharpes"]) == M.shape[1]


def test_walk_forward_pbo_real_when_multiple_configs():
    """FU1 — pbo(oos_perf_matrix) is a real [0,1] probability when C>=2."""
    from bt.panel.stats import pbo

    p = _panel(T=96, N=4)
    res = walk_forward(
        p,
        strategy_factory=_long_one_asset_strategy,
        search_space={"asset": {"values": [0, 1, 2, 3]}},
        run_cfg=_WF_CFG,
        scheme="rolling",
        n_folds=3,
        embargo_bars=1,
        purge_bars=1,
        budget=4,
        periods_per_year=252.0,
        session_aware=False,
    )
    M = np.asarray(res["oos_perf_matrix"], dtype=np.float64)
    assert M.shape[1] >= 2
    pb = pbo(M, n_splits=8)
    assert 0.0 <= pb <= 1.0


def test_walk_forward_freezes_params_on_test():
    """Tuned params are frozen from train and reused verbatim on test (no
    re-tuning on test data)."""
    p = _panel(T=60)
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
    res = walk_forward(
        p,
        strategy_factory=_const_long_strategy,
        search_space={"w": {"min": 0.1, "max": 0.9}},
        run_cfg=cfg,
        scheme="rolling",
        n_folds=2,
        embargo_bars=0,
        purge_bars=0,
        budget=3,
        periods_per_year=252.0,
        session_aware=False,
    )
    for fr in res["folds"]:
        assert set(fr["best_params"].keys()) == {"w"}
        assert 0.1 <= fr["best_params"]["w"] <= 0.9
