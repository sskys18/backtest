"""P2.5 — metrics core + mode-gated annualization (spec §8, C6/C10).

All metrics are derived from the existing ``run_panel`` result dict plus the
panel arrays the engine does not echo back (bar timestamps for Δt, the final
per-asset close + fx for unrealized attribution). No Rust change: P2.5 is
Python-only.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from bt.panel.metrics import _long_short_attribution, panel_metrics


# ---------------------------------------------------------------------------
# P2.5.2 — daily annualization
# ---------------------------------------------------------------------------
def test_sharpe_daily_annualization():
    """Known equity → Sharpe == μ/σ·sqrt(252) to 1e-9 (session_aware=False)."""
    # Construct an equity curve whose simple per-bar returns are known.
    rets = np.array([0.01, -0.005, 0.02, 0.0, -0.01, 0.015])
    equity = [100_000.0]
    for r in rets:
        equity.append(equity[-1] * (1.0 + r))
    equity = np.asarray(equity)

    result = {
        "equity": equity,
        "gross_exposure": np.ones(len(equity)),
        "net_exposure": np.zeros(len(equity)),
        "funding_pnl": np.zeros(len(equity)),
        "turnover": np.zeros(len(equity)),
        "realized_pnl": np.zeros(len(equity)),
        "trades": [],
    }
    # daily bars, 1 day = 24h apart (irrelevant for daily Sharpe)
    timestamps = np.arange(len(equity), dtype=np.int64) * 86_400_000

    m = panel_metrics(
        result,
        timestamps=timestamps,
        close=np.full((len(equity), 1), 100.0),
        fx=np.ones((len(equity), 1)),
        assets=["A"],
        session_aware=False,
        periods_per_year=252,
    )

    r = np.diff(equity) / equity[:-1]
    mu = r.mean()
    sd = r.std(ddof=1)
    expected = mu / sd * math.sqrt(252)
    assert abs(m["sharpe"] - expected) < 1e-9


def test_max_dd():
    """Crafted equity curve → exact max drawdown (peak-to-trough fraction)."""
    equity = np.array([100.0, 120.0, 90.0, 130.0, 110.0, 80.0, 150.0])
    # running peak: 100,120,120,130,130,130,150
    # dd at idx5 (80) vs peak 130 → (80-130)/130 = -0.3846...
    result = {
        "equity": equity,
        "gross_exposure": np.ones(len(equity)),
        "net_exposure": np.zeros(len(equity)),
        "funding_pnl": np.zeros(len(equity)),
        "turnover": np.zeros(len(equity)),
        "realized_pnl": np.zeros(len(equity)),
        "trades": [],
    }
    timestamps = np.arange(len(equity), dtype=np.int64) * 86_400_000
    m = panel_metrics(
        result,
        timestamps=timestamps,
        close=np.full((len(equity), 1), 100.0),
        fx=np.ones((len(equity), 1)),
        assets=["A"],
        session_aware=False,
        periods_per_year=252,
    )
    expected = (80.0 - 130.0) / 130.0
    assert abs(m["max_dd"] - expected) < 1e-12


# ---------------------------------------------------------------------------
# P2.5.2 — intraday time-neutral Sharpe
# ---------------------------------------------------------------------------
def test_time_neutral_sharpe():
    """Intraday series with known h_t → μ_h/σ_h·sqrt(A); flat bars excluded."""
    # 4 return bars; bar 2 (idx) is flat (gross==0) → h=0, excluded from
    # time-neutral moments but stays in the raw series.
    equity = np.array([100_000.0, 101_000.0, 100_500.0, 100_500.0, 101_500.0])
    gross = np.array([0.0, 1.0, 1.0, 0.0, 1.0])  # gross at each bar
    # bar hours: timestamps spaced so Δt differs (overnight vs intraday)
    # Δt(hours) between consecutive bars: [6, 6, 27.7, 6] for r-bars 1..4
    hours_ms = np.array([0.0, 6.0, 12.0, 39.7, 45.7]) * 3_600_000
    timestamps = hours_ms.astype(np.int64)

    result = {
        "equity": equity,
        "gross_exposure": gross,
        "net_exposure": np.zeros(len(equity)),
        "funding_pnl": np.zeros(len(equity)),
        "turnover": np.zeros(len(equity)),
        "realized_pnl": np.zeros(len(equity)),
        "trades": [],
    }
    m = panel_metrics(
        result,
        timestamps=timestamps,
        close=np.full((len(equity), 1), 100.0),
        fx=np.ones((len(equity), 1)),
        assets=["A"],
        session_aware=True,
        periods_per_year=365,
        hours_per_year=8760.0,
    )

    # hand calc
    r = np.diff(equity) / equity[:-1]               # 4 returns
    dt_h = np.diff(timestamps) / 3_600_000.0        # [6,6,27.7,6]
    gross_at_bar = gross[1:]                         # gross aligned to return bar t
    h = dt_h * (gross_at_bar > 0)                    # flat bar → 0
    sumh = h.sum()
    mu_h = (r * h).sum() / sumh
    sig_h = math.sqrt((h * (r - mu_h) ** 2).sum() / sumh)
    exposed = h > 0
    A = 8760.0 / h[exposed].mean()
    expected = mu_h / sig_h * math.sqrt(A)

    assert abs(m["time_neutral_sharpe"] - expected) < 1e-9


def test_exposure_coverage_reported():
    """Coverage = Σ exposed-bar hours / Σ wall-clock hours."""
    equity = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
    gross = np.array([0.0, 1.0, 0.0, 1.0, 1.0])
    hours_ms = np.array([0.0, 6.0, 12.0, 18.0, 24.0]) * 3_600_000
    timestamps = hours_ms.astype(np.int64)
    result = {
        "equity": equity,
        "gross_exposure": gross,
        "net_exposure": np.zeros(len(equity)),
        "funding_pnl": np.zeros(len(equity)),
        "turnover": np.zeros(len(equity)),
        "realized_pnl": np.zeros(len(equity)),
        "trades": [],
    }
    m = panel_metrics(
        result,
        timestamps=timestamps,
        close=np.full((len(equity), 1), 100.0),
        fx=np.ones((len(equity), 1)),
        assets=["A"],
        session_aware=True,
        periods_per_year=365,
        hours_per_year=8760.0,
    )
    dt_h = np.diff(timestamps) / 3_600_000.0   # [6,6,6,6]
    gross_at_bar = gross[1:]
    exposed_hours = (dt_h * (gross_at_bar > 0)).sum()  # bars 1,3,4 exposed → 18
    wall = dt_h.sum()                                   # 24
    assert abs(m["exposure_coverage"] - exposed_hours / wall) < 1e-12


# ---------------------------------------------------------------------------
# P2.5.1 — long/short attribution
# ---------------------------------------------------------------------------
def test_long_short_attribution():
    """long + short attribution == equity[-1] − equity[0] (zero-cost, fx=1).

    Reconstructed from recorded fill prices in ``trades`` so the tie-out holds
    by construction with funding/fees/slippage = 0.
    """
    from bt.bt_engine import run_panel

    t, n = 6, 2
    # Asset A trends up, asset B trends down — a long-A / short-B book makes
    # money on BOTH legs, so the two buckets are individually nonzero.
    close = np.zeros((t, n))
    close[:, 0] = np.array([100.0, 102.0, 104.0, 106.0, 108.0, 110.0])  # A up
    close[:, 1] = np.array([100.0, 98.0, 96.0, 94.0, 92.0, 90.0])       # B down
    open_ = close.copy()
    funding = np.zeros((t, n))
    fx = np.ones((t, n))
    active = np.ones((t, n))
    sessions = np.full((t, n), 2, dtype=np.int64)
    decision_time = np.arange(t, dtype=np.int64)

    weights = np.zeros((t, n))
    weights[:, 0] = 0.5    # long A
    weights[:, 1] = -0.5   # short B

    cfg = {
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
    out = run_panel(
        ["A", "B"],
        close, open_, funding, fx, weights, active, sessions, decision_time,
        cfg,
    )

    m = panel_metrics(
        out,
        timestamps=decision_time,
        close=close,
        fx=fx,
        assets=["A", "B"],
        session_aware=False,
        periods_per_year=252,
    )

    total = out["equity"][-1] - out["equity"][0]
    assert abs(m["long_pnl"] + m["short_pnl"] - total) < 1e-6
    # both legs profit in this scenario
    assert m["long_pnl"] > 0.0
    assert m["short_pnl"] > 0.0


def test_long_short_attribution_joint_basis_varying_fx():
    """Joint numeraire basis (px·fx) must tie out when px AND fx vary at entry.

    Regression for the cost-basis bug the engine's P1 design exists for: when
    two same-direction entry fills land at *different* px and *different* fx,
    ``(avg px)·(avg fx) ≠ avg(px·fx)``. The reconstruction must accumulate the
    single joint basis ``Σ Δq·px·fx`` (engine ``cost_basis_num``); otherwise the
    unrealized mark on the held position mis-states PnL.

    This drives ``_long_short_attribution`` directly with the discriminating
    trade pattern (two adds, then hold to an unrealized mark) — the integration
    path with monotone-reduce fills does NOT exercise the joint-basis term.

    Numbers (one long asset):
      * open 10 @ px=100, fx=1.0  → basis 1000
      * add  10 @ px=120, fx=2.0  → basis += 2400 → 3400 (qty 20)
      * mark close=130, fx=2.0    → value 20·130·2 = 5200
      * true unrealized = 5200 − 3400 = 1800  (all in the LONG bucket)
    The two-average form yields 1900 here (residual +100); the joint basis is
    exact.
    """
    trades = [
        {"asset": 0, "qty": 10.0, "price": 100.0, "fx": 1.0},
        {"asset": 0, "qty": 10.0, "price": 120.0, "fx": 2.0},
    ]
    close = np.array([[130.0]])
    fx = np.array([[2.0]])

    long_pnl, short_pnl = _long_short_attribution(trades, close, fx, 1)

    assert abs(long_pnl - 1800.0) < 1e-9
    assert abs(short_pnl - 0.0) < 1e-9


def test_long_short_attribution_short_leg_varying_fx():
    """Short-leg joint basis: realize on partial close at varying px·fx.

    Open short, then partially cover at a different px·fx, then mark the rest.
    Confirms the short bucket carries the joint basis (no double-sign) and
    realized + unrealized sum to the true short PnL.

      * open −10 @ px=100, fx=1.0  → basis −1000
      * cover +6 @ px=90,  fx=2.0  → cb_unit = −1000/−10 = 100;
        realized = closed·(px·fx − cb_unit) = (−10−(−4))·(180 − 100)
                 = (−6)·80 = −480  → short bucket
        remaining basis = −4·100 = −400 (qty −4)
      * mark close=80, fx=2.0      → unrealized = −4·80·2 − (−400)
                 = −640 + 400 = −240  → short bucket
      * total short PnL = −480 + −240 = −720
    """
    trades = [
        {"asset": 0, "qty": -10.0, "price": 100.0, "fx": 1.0},
        {"asset": 0, "qty": 6.0, "price": 90.0, "fx": 2.0},
    ]
    close = np.array([[80.0]])
    fx = np.array([[2.0]])

    long_pnl, short_pnl = _long_short_attribution(trades, close, fx, 1)

    assert abs(long_pnl - 0.0) < 1e-9
    assert abs(short_pnl - (-720.0)) < 1e-9


def test_long_short_attribution_nan_final_mark_skipped():
    """NaN final close contributes 0 unrealized — mirrors engine compute_unrealized.

    The Rust engine's ``compute_unrealized`` (portfolio_engine.rs L785) guards
    ``if c.is_nan() {{ continue; }}``: a nonzero position carried into a final
    bar with a NaN close (delisting / data gap / trading halt — the primary
    KR-altcoin condition) contributes 0 unrealized, and the engine reports
    FINITE PnL. The Python reconstruction must mirror this; without the guard
    ``q·NaN − cb = NaN`` poisons the bucket and breaks the tie-out for ANY path
    that leaves a held position into a NaN final mark.

      * open 10 @ px=100, fx=1.0  → basis 1000, qty 10 (long)
      * mark close=NaN, fx=1.0    → unrealized SKIPPED (engine parity) → 0
    Asset B is clean and must be unaffected.
    """
    trades = [
        {"asset": 0, "qty": 10.0, "price": 100.0, "fx": 1.0},   # long, NaN final
        {"asset": 1, "qty": 5.0, "price": 50.0, "fx": 1.0},     # long, clean
    ]
    close = np.array([[math.nan, 60.0]])   # asset 0 final close is NaN
    fx = np.array([[1.0, 1.0]])

    long_pnl, short_pnl = _long_short_attribution(trades, close, fx, 2)

    # asset 0 contributes 0 (NaN mark skipped); asset 1 contributes 5·(60−50)=50
    assert math.isfinite(long_pnl), "NaN final mark must not poison the bucket"
    assert abs(long_pnl - 50.0) < 1e-9
    assert abs(short_pnl - 0.0) < 1e-9


def test_long_short_attribution_nan_final_close_ties_to_engine():
    """End-to-end engine parity: long held into a NaN final close still ties out.

    The contract under test is engine parity — ``long_pnl + short_pnl ==
    equity[-1] − equity[0]`` for ANY path, including one that carries a nonzero
    position into a final bar with a NaN close (delisting / data gap on the last
    bar). The bar-(T-1) rebalance-to-zero enqueues a flatten that fills at bar-T
    open, but bar T is the LAST bar so the order never drains → the position
    survives nonzero into the NaN final mark. The engine guards NaN in both
    rebalance (L561) and ``compute_unrealized`` (L785), so ``equity[-1]`` stays
    FINITE; the Python reconstruction must mirror that or the tie-out breaks.
    """
    from bt.bt_engine import run_panel

    t, n = 4, 2
    close = np.zeros((t, n))
    close[:, 0] = np.array([100.0, 102.0, 104.0, math.nan])  # A: NaN final close
    close[:, 1] = np.array([100.0, 101.0, 102.0, 103.0])     # B: clean
    open_ = close.copy()
    funding = np.zeros((t, n))
    fx = np.ones((t, n))
    active = np.ones((t, n))
    active[t - 1, 0] = 0.0        # A inactive on the final (NaN) bar
    sessions = np.full((t, n), 2, dtype=np.int64)
    decision_time = np.arange(t, dtype=np.int64)

    weights = np.zeros((t, n))
    weights[:, 0] = 0.5    # long A throughout
    weights[t - 1, 0] = 0.0  # rebalance A to zero on the last bar (never drains)
    weights[:, 1] = 0.5    # long B

    cfg = {
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
    out = run_panel(
        ["A", "B"],
        close, open_, funding, fx, weights, active, sessions, decision_time,
        cfg,
    )

    # engine handles the NaN cleanly → finite terminal equity
    assert math.isfinite(out["equity"][-1])

    m = panel_metrics(
        out,
        timestamps=decision_time,
        close=close,
        fx=fx,
        assets=["A", "B"],
        session_aware=False,
        periods_per_year=252,
    )

    total = out["equity"][-1] - out["equity"][0]
    assert math.isfinite(m["long_pnl"])
    assert abs(m["long_pnl"] + m["short_pnl"] - total) < 1e-6


def test_long_short_attribution_nan_fx_propagates():
    """A NaN final fx PROPAGATES into the bucket — engine parity, not a skip.

    The Rust engine's ``compute_unrealized`` (portfolio_engine.rs L784-789)
    guards the CLOSE only and then computes ``q·close·fxv − cost_basis``; a NaN
    fx flows straight through. The Python reconstruction must mirror this: with
    a finite close and a NaN final fx the unrealized leg is NaN, so the bucket
    is NaN — it must NOT be suppressed to 0 (the prior fx-skip clause broke the
    tie-out in exactly this case by reporting a finite bucket where the engine
    reports NaN equity).
    """
    trades = [
        {"asset": 0, "qty": 10.0, "price": 100.0, "fx": 1.0},
    ]
    close = np.array([[110.0]])   # finite close
    fx = np.array([[math.nan]])   # NaN final fx → engine propagates NaN

    long_pnl, short_pnl = _long_short_attribution(trades, close, fx, 1)

    assert math.isnan(long_pnl), "NaN fx must propagate (engine parity), not be skipped"
    assert short_pnl == 0.0


def test_core_metrics_keys_present():
    """All §8 core metrics are reported."""
    equity = np.array([100.0, 101.0, 100.0, 102.0])
    result = {
        "equity": equity,
        "gross_exposure": np.ones(len(equity)),
        "net_exposure": np.zeros(len(equity)),
        "funding_pnl": np.array([0.0, 1.0, -0.5, 0.2]),
        "turnover": np.array([0.0, 0.1, 0.0, 0.2]),
        "realized_pnl": np.zeros(len(equity)),
        "trades": [],
    }
    timestamps = np.arange(len(equity), dtype=np.int64) * 86_400_000
    m = panel_metrics(
        result,
        timestamps=timestamps,
        close=np.full((len(equity), 1), 100.0),
        fx=np.ones((len(equity), 1)),
        assets=["A"],
        session_aware=False,
        periods_per_year=252,
    )
    for k in (
        "sharpe", "sortino", "max_dd", "turnover_total", "funding_pnl_total",
        "hit_rate", "long_pnl", "short_pnl", "gross_exposure_mean",
        "net_exposure_mean",
    ):
        assert k in m, f"missing metric {k}"
