"""P2 — PanelView + trailing-only helpers (C4).

Tests the strategy-layer scaffolding: PanelView wraps aligned arrays, exposes
session/sector/funding, and the helpers (rank, sector_demean, vol_scale,
rolling) are trailing-only by default. A forward-looking helper call raises
LookaheadError.
"""

from __future__ import annotations

import numpy as np
import pytest

from bt.panel.loader import Panel
from bt.panel.strategy import LookaheadError, PanelView, WeightMatrix


def _panel(t: int, n: int, *, sector=None) -> Panel:
    close = np.cumsum(np.ones((t, n)), axis=0) + 100.0
    sector_arr = np.empty((t, n), dtype=object)
    sector_arr[:] = sector if sector is not None else "X"
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


def test_panelview_exposes_aligned_arrays():
    pv = PanelView(_panel(5, 3))
    assert pv.close.shape == (5, 3)
    assert pv.session.shape == (5, 3)
    assert pv.funding.shape == (5, 3)
    assert pv.sector.shape == (5, 3)
    assert pv.assets == ["A0", "A1", "A2"]


def test_trailing_helper_no_lookahead():
    """`rolling`/`rank` at row t use only rows <= t."""
    pv = PanelView(_panel(6, 2))
    roll = pv.rolling(pv.close, window=3, op="mean")
    # Row 2 mean must equal mean of rows 0..2 of close (trailing window).
    expected = pv.close[0:3].mean(axis=0)
    np.testing.assert_allclose(roll[2], expected)
    # First (window-1) rows are NaN (insufficient trailing history).
    assert np.isnan(roll[0]).all()
    assert np.isnan(roll[1]).all()


def test_rank_is_cross_sectional_per_row():
    pv = PanelView(_panel(3, 4))
    scores = np.array([[1.0, 4.0, 2.0, 3.0]] * 3)
    r = pv.rank(scores)
    # Highest score → highest rank; per row, independent of other rows.
    assert r[0].argmax() == 1
    assert r[0].argmin() == 0


def test_centered_window_rejected():
    """A centered (forward-looking) rolling window must raise."""
    pv = PanelView(_panel(6, 2))
    with pytest.raises(LookaheadError):
        pv.rolling(pv.close, window=3, op="mean", center=True)


def test_negative_lag_rejected():
    """Negative lag = peeking into the future → reject."""
    pv = PanelView(_panel(6, 2))
    with pytest.raises(LookaheadError):
        pv.lag(pv.close, -1)


def test_weight_matrix_holds_shape():
    pv = PanelView(_panel(4, 2))
    w = WeightMatrix(np.zeros((4, 2)), assets=pv.assets)
    assert w.W.shape == (4, 2)
    assert w.assets == ["A0", "A1"]
