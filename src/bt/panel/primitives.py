"""P2 — Strategy primitives: CrossSectionalStrategy (§4.1), PairsStrategy (§4.2).

Both are :class:`bt.panel.strategy.PanelStrategy` implementations producing a
validated :class:`WeightMatrix`. All feature computation routes through the
trailing-only :class:`PanelView` helpers (no raw forward indexing).
"""

from __future__ import annotations

import numpy as np

from bt.panel.strategy import PanelView, WeightMatrix


class CrossSectionalStrategy:
    """Cross-sectional rotation (§4.1), fixed order per rebalance bar ``t``:

    1. score each active asset (trailing ``lookback``-bar return),
    2. rank → top-``k`` long / bottom-``k`` short,
    3. raw weight (``equal`` / ``score_prop`` / ``inverse_vol``),
    4. optional vol-scale overlay (``equal`` / ``score_prop`` base only),
    5. optional sector-neutralize (demean within sector, applied AFTER the
       vol-scale overlay so the per-sector zero-sum survives — see ``compute``),
    6. cap/renormalize to ``leverage_cap``,
    7. optional time/regime gate (applied last; zeroes weights when closed).
    """

    def __init__(
        self,
        *,
        lookback: int,
        top_k: int,
        bottom_k: int,
        leverage_cap: float = 1.0,
        weighting: str = "equal",
        sector_neutral: bool = False,
        vol_window: int | None = None,
        gate_session: int | None = None,
    ):
        self.lookback = lookback
        self.top_k = top_k
        self.bottom_k = bottom_k
        self.leverage_cap = leverage_cap
        self.weighting = weighting
        self.sector_neutral = sector_neutral
        self.vol_window = vol_window
        self.gate_session = gate_session

    def compute(self, panel: PanelView) -> WeightMatrix:
        close = panel.close
        t, n = close.shape
        W = np.zeros((t, n), dtype=np.float64)

        # Trailing momentum score: lookback-bar return, knowable at t.
        past = panel.lag(close, self.lookback)
        score = close / past - 1.0  # NaN for the first `lookback` rows

        vol = None
        if self.weighting == "inverse_vol" or self.vol_window is not None:
            w = self.vol_window or self.lookback
            ret = close / panel.lag(close, 1) - 1.0
            vol = panel.rolling(ret, window=w, op="std")

        for ti in range(t):
            row = score[ti]
            active = panel.active_mask[ti] & ~np.isnan(row)
            idx = np.where(active)[0]
            if idx.size == 0:
                continue
            order = idx[np.argsort(row[idx])]  # ascending score
            longs = order[-self.top_k :] if self.top_k > 0 else np.array([], int)
            shorts = order[: self.bottom_k] if self.bottom_k > 0 else np.array([], int)
            # Guard against overlap when universe is tiny.
            shorts = np.array([j for j in shorts if j not in set(longs)], dtype=int)

            raw = np.zeros(n, dtype=np.float64)
            self._assign_leg(raw, longs, +1.0, row, vol[ti] if vol is not None else None)
            self._assign_leg(raw, shorts, -1.0, row, vol[ti] if vol is not None else None)

            # Step 4 — optional vol-scale overlay (§4.1). A separate post-selection
            # multiplier on TOP of the base weighting. For `inverse_vol` the vol is
            # already baked into step 3, so applying it again would be 1/vol²; only
            # overlay for the equal/score_prop base weightings. This runs BEFORE
            # sector_demean so the per-sector zero-sum (step 5) is the LAST shaping
            # transform: vol_scale multiplies elementwise by distinct 1/vol factors,
            # which would otherwise destroy a zero-sum demeaned vector (regression
            # caught by the panel verifier — see test_cs_vol_scale_sector_neutral).
            if self.vol_window is not None and self.weighting != "inverse_vol":
                raw = panel.vol_scale(raw, vol[ti])

            # Step 5 — optional sector-neutralize. Applied AFTER the vol-scale
            # overlay so within-sector Σ(W)≈0 holds for the shipped weights.
            if self.sector_neutral:
                raw = panel.sector_demean(raw, panel.sector[ti])

            gross = np.abs(raw).sum()
            if gross > 0:
                raw = raw / gross * self.leverage_cap

            if self.gate_session is not None:
                # Zero weights where the bar's session != the gate session.
                if int(panel.session[ti].max()) != self.gate_session:
                    raw[:] = 0.0

            W[ti] = raw

        return WeightMatrix(W, assets=panel.assets)

    def _assign_leg(self, raw, idx, sign, score_row, vol_row):
        if idx.size == 0:
            return
        if self.weighting == "equal":
            raw[idx] = sign * 1.0
        elif self.weighting == "score_prop":
            mag = np.abs(score_row[idx])
            s = mag.sum()
            raw[idx] = sign * (mag / s if s > 0 else 1.0 / idx.size)
        elif self.weighting == "inverse_vol":
            v = vol_row[idx] if vol_row is not None else np.ones(idx.size)
            inv = np.where((v > 0) & np.isfinite(v), 1.0 / v, 0.0)
            s = inv.sum()
            raw[idx] = sign * (inv / s if s > 0 else 1.0 / idx.size)
        else:
            raise ValueError(f"unknown weighting {self.weighting!r}")


class PairsStrategy:
    """Dollar-neutral pair (§4.2) on the spread z-score of two legs.

    Long ``leg_long`` / short ``leg_short`` when the spread z-score crosses
    ``-entry_z`` (spread cheap), flip when it crosses ``+entry_z``. ``W`` sums to
    ~0 (net-neutral); gross = ``gross``.
    """

    def __init__(
        self,
        *,
        leg_long: int,
        leg_short: int,
        lookback: int,
        entry_z: float = 1.0,
        gross: float = 1.0,
    ):
        self.leg_long = leg_long
        self.leg_short = leg_short
        self.lookback = lookback
        self.entry_z = entry_z
        self.gross = gross

    def compute(self, panel: PanelView) -> WeightMatrix:
        close = panel.close
        t, n = close.shape
        W = np.zeros((t, n), dtype=np.float64)

        spread = close[:, self.leg_long] - close[:, self.leg_short]
        spread2d = spread.reshape(-1, 1)
        z = panel.zscore(spread2d, as_of="trailing", window=self.lookback).ravel()

        half = self.gross / 2.0
        for ti in range(t):
            zt = z[ti]
            if np.isnan(zt):
                continue
            if zt <= -self.entry_z:
                # spread cheap → long leg_long, short leg_short
                W[ti, self.leg_long] = +half
                W[ti, self.leg_short] = -half
            elif zt >= self.entry_z:
                # spread rich → short leg_long, long leg_short
                W[ti, self.leg_long] = -half
                W[ti, self.leg_short] = +half

        return WeightMatrix(W, assets=panel.assets)
