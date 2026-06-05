"""P2 — CrossSectionalStrategy (§4.1) + PairsStrategy (§4.2)."""

from __future__ import annotations

import numpy as np

from bt.panel.loader import Panel
from bt.panel.primitives import CrossSectionalStrategy, PairsStrategy
from bt.panel.run import run_panel_strategy
from bt.panel.strategy import PanelView


def _panel(close: np.ndarray, *, sector=None) -> Panel:
    t, n = close.shape
    sector_arr = np.empty((t, n), dtype=object)
    if sector is None:
        sector_arr[:] = "X"
    else:
        for j in range(n):
            sector_arr[:, j] = sector[j]
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
        sector=sector_arr,
        active_mask=np.ones((t, n), dtype=bool),
        numeraire="USD",
        numeraire_is_unit_fx=True,
    )


def _momentum_close(t: int, n: int) -> np.ndarray:
    # Each asset trends at a distinct slope → stable cross-sectional ranking.
    slopes = np.linspace(0.1, 1.0, n)
    base = np.arange(t).reshape(-1, 1) * slopes.reshape(1, -1)
    return 100.0 + base


def test_cs_rotation_weights_valid():
    """W respects Σ|W| <= leverage_cap and top-k long / bottom-k short."""
    t, n = 10, 6
    pv = PanelView(_panel(_momentum_close(t, n)))
    strat = CrossSectionalStrategy(
        lookback=5, top_k=2, bottom_k=2, leverage_cap=1.0, weighting="equal"
    )
    wm = strat.compute(pv)
    W = wm.W
    assert W.shape == (t, n)
    last = W[-1]
    gross = np.abs(last).sum()
    assert gross <= 1.0 + 1e-9
    # Highest-momentum assets (high index) long, lowest short.
    assert (last[-2:] > 0).all()   # top-2 momentum long
    assert (last[:2] < 0).all()    # bottom-2 momentum short
    # exactly top_k + bottom_k non-zero.
    assert np.count_nonzero(last) == 4


def test_sector_demean_neutral():
    """Within-sector weights sum ~0 after sector neutralization."""
    t, n = 8, 6
    sectors = ["tech", "tech", "tech", "fin", "fin", "fin"]
    pv = PanelView(_panel(_momentum_close(t, n), sector=sectors))
    strat = CrossSectionalStrategy(
        lookback=4, top_k=3, bottom_k=3, leverage_cap=2.0,
        weighting="equal", sector_neutral=True,
    )
    wm = strat.compute(pv)
    last = wm.W[-1]
    tech = last[:3].sum()
    fin = last[3:].sum()
    assert abs(tech) < 1e-9
    assert abs(fin) < 1e-9


def test_pairs_net_neutral():
    """Pairs W net ~0 (dollar-neutral)."""
    t = 30
    rng = np.random.default_rng(0)
    a = 100.0 + np.cumsum(rng.normal(0, 1, t))
    spread = rng.normal(0, 1, t)
    b = a + spread  # cointegrated-ish: b tracks a plus mean-zero spread
    close = np.column_stack([a, b])
    pv = PanelView(_panel(close))
    strat = PairsStrategy(
        leg_long=0, leg_short=1, lookback=10, entry_z=1.0, gross=1.0
    )
    wm = strat.compute(pv)
    net = wm.W.sum(axis=1)
    assert np.abs(net).max() < 1e-9
    # at least one bar takes a position.
    assert np.abs(wm.W).sum() > 0


def test_cs_vol_scale_applied():
    """Step-4 vol-scale overlay: equal weighting + vol_window must vol-scale.

    With weighting='equal' all selected legs start at equal magnitude; the
    optional vol-scale overlay (§4.1 step 4) then multiplies each by 1/vol, so a
    higher-vol asset gets a SMALLER final weight. Without vol_window the weights
    are plain equal. This is the path the verifier found unwired.
    """
    t, n = 40, 4
    rng = np.random.default_rng(3)
    # Two assets calm, two volatile — but all uptrending so all rank as longs
    # is not guaranteed; use top_k=2,bottom_k=0 to take the 2 highest-momentum.
    # Build monotonically rising closes with distinct per-asset noise scale.
    base = np.arange(t).reshape(-1, 1) * np.linspace(1.0, 1.3, n).reshape(1, -1)
    noise = rng.normal(0, 1, (t, n)) * np.array([0.1, 0.1, 3.0, 3.0])
    close = 100.0 + base + np.cumsum(noise, axis=0)

    plain = CrossSectionalStrategy(
        lookback=5, top_k=4, bottom_k=0, leverage_cap=1.0, weighting="equal"
    )
    scaled = CrossSectionalStrategy(
        lookback=5, top_k=4, bottom_k=0, leverage_cap=1.0,
        weighting="equal", vol_window=10,
    )
    W_plain = plain.compute(PanelView(_panel(close))).W
    W_scaled = scaled.compute(PanelView(_panel(close))).W

    # vol_window must NOT be a silent no-op.
    assert not np.allclose(W_plain, W_scaled), "vol_window had no effect (step-4 unwired)"

    # On the last bar, all 4 selected, equal-then-vol-scaled: the lower-vol
    # assets (cols 0,1) should carry MORE weight than the high-vol assets (2,3).
    last = np.abs(W_scaled[-1])
    assert last[0] > last[2]
    assert last[1] > last[3]
    # Still gross-normalized to leverage_cap.
    assert abs(np.abs(W_scaled[-1]).sum() - 1.0) < 1e-9


def test_cs_vol_scale_sector_neutral():
    """Regression (panel verifier): equal + sector_neutral=True + vol_window must
    still ship within-sector Σ(W)≈0.

    The step-4 vol-scale overlay multiplies each leg by a distinct 1/vol factor,
    which destroys a per-sector zero-sum if it runs AFTER sector_demean. Sectors
    are INTERLEAVED so each sector spans both a long and a short leg — otherwise a
    same-sign sector demeans to all-zeros and the test passes trivially even on
    the buggy ordering. Asserts only within-sector Σ≈0 (magnitudes differ between
    valid orderings, so pinning weights would over-constrain).
    """
    t, n = 30, 6
    # Interleave: tech={0,2,4}, fin={1,3,5}. _momentum_close ranks by slope, so
    # cols 4,5 (highest) go long and 0,1 (lowest) go short → each sector mixed-sign.
    sectors = ["tech", "fin", "tech", "fin", "tech", "fin"]
    pv = PanelView(_panel(_momentum_close(t, n), sector=sectors))
    strat = CrossSectionalStrategy(
        lookback=10, top_k=3, bottom_k=3, leverage_cap=1.0,
        weighting="equal", sector_neutral=True, vol_window=10,
    )
    last = strat.compute(pv).W[-1]
    assert np.abs(last).sum() > 0, "all-zero weights would pass Σ≈0 trivially"
    tech = last[[0, 2, 4]].sum()
    fin = last[[1, 3, 5]].sum()
    assert abs(tech) < 1e-9, f"within-sector tech Σ={tech} (vol_scale broke neutrality)"
    assert abs(fin) < 1e-9, f"within-sector fin Σ={fin} (vol_scale broke neutrality)"


def test_end_to_end_daily_rotation():
    """Load synthetic daily panel → CS rotation → run_panel → equity curve."""
    t, n = 12, 5
    panel = _panel(_momentum_close(t, n))
    strat = CrossSectionalStrategy(
        lookback=5, top_k=1, bottom_k=1, leverage_cap=1.0, weighting="equal"
    )
    out = run_panel_strategy(
        panel,
        strat,
        cfg={
            "initial_collateral": 100_000.0,
            "fee_rate": 0.0,
            "slippage_bps": 0.0,
            "im_rate": 0.1,
            "mm_rate": 0.05,
            "leverage_cap": 1.0,
            "max_net": 1.0,
            "max_per_asset": 1.0,
            "rebalance_freq": 1,
            "fill_model": "next_bar",
            "session_aware": False,
            "on_violation": "reject",
            "cap_kind": "hard",
            "numeraire_is_unit_fx": True,
        },
    )
    assert len(out["equity"]) == t
    assert all(np.isfinite(out["equity"]))
