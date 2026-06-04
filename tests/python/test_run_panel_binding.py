"""P1 — 2D run_panel PyO3 binding smoke test."""
from __future__ import annotations

import numpy as np
import pytest


def _base_cfg():
    return {
        "initial_collateral": 100_000.0,
        "fee_rate": 0.0,
        "slippage_bps": 0.0,
        "im_rate": 0.1,
        "mm_rate": 0.05,
        "leverage_cap": 2.0,
        "max_net": 2.0,
        "max_per_asset": 2.0,
        "rebalance_freq": 1,
        "fill_model": "next_bar",
        "session_aware": False,
        "on_violation": "reject",
        "cap_kind": "hard",
        "numeraire_is_unit_fx": True,
    }


def _panel(t: int, n: int):
    close = np.full((t, n), 100.0)
    open_ = np.full((t, n), 100.0)
    funding = np.zeros((t, n))
    fx = np.ones((t, n))
    active = np.ones((t, n))
    sessions = np.full((t, n), 2, dtype=np.int64)
    decision_time = np.arange(t, dtype=np.int64)
    return close, open_, funding, fx, active, sessions, decision_time


def test_run_panel_returns_dict_with_equity():
    from bt.bt_engine import run_panel

    t, n = 4, 2
    close, open_, funding, fx, active, sessions, dt = _panel(t, n)
    weights = np.zeros((t, n))
    weights[:, 0] = 0.5  # long A half-equity

    out = run_panel(
        ["A", "B"],
        close, open_, funding, fx, weights, active, sessions, dt,
        _base_cfg(),
    )
    assert set(out.keys()) >= {
        "equity", "gross_exposure", "net_exposure",
        "funding_pnl", "turnover", "realized_pnl", "trades",
    }
    assert len(out["equity"]) == t
    assert len(out["gross_exposure"]) == t
    assert len(out["net_exposure"]) == t
    # flat prices → equity stays at initial collateral.
    assert abs(out["equity"][-1] - 100_000.0) < 1e-6


def test_run_panel_wrong_assets_len_raises():
    from bt.bt_engine import run_panel

    t, n = 3, 2
    close, open_, funding, fx, active, sessions, dt = _panel(t, n)
    weights = np.zeros((t, n))
    with pytest.raises(Exception):
        run_panel(
            ["A"],  # len 1 != N=2
            close, open_, funding, fx, weights, active, sessions, dt,
            _base_cfg(),
        )


def test_run_panel_bias_forbidden_by_default():
    from bt.bt_engine import run_panel

    t, n = 3, 1
    close, open_, funding, fx, active, sessions, dt = _panel(t, n)
    weights = np.zeros((t, n))
    bias = np.full((t, n), 0.1)
    available_at = np.array([0, 1, 2], dtype=np.int64)
    # bias_mode defaults to "forbidden" → any bias rejected.
    with pytest.raises(Exception):
        run_panel(
            ["A"],
            close, open_, funding, fx, weights, active, sessions, dt,
            _base_cfg(),
            bias, available_at,  # bias_mode omitted → forbidden
        )


def test_run_panel_bias_valid_pit_accepted():
    from bt.bt_engine import run_panel

    t, n = 3, 1
    close, open_, funding, fx, active, sessions, dt = _panel(t, n)
    weights = np.zeros((t, n))
    bias = np.full((t, n), 0.5)
    available_at = np.array([0, 1, 2], dtype=np.int64)  # all <= decision_time
    out = run_panel(
        ["A"],
        close, open_, funding, fx, weights, active, sessions, dt,
        _base_cfg(),
        bias, available_at, "backtest",
    )
    # tilt applied → a trade opened.
    assert len(out["trades"]) >= 1
