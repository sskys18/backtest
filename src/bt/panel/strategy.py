"""P2 — PanelStrategy API + PanelView + trailing-only helpers (C4).

The signal layer. A ``PanelStrategy`` consumes a :class:`PanelView` (aligned
``[T,N]`` arrays + session/sector/funding) and returns a :class:`WeightMatrix`
``W[T,N]`` of target equity fractions in ``[-1, +1]``.

C4 lookahead guard — honest scope
---------------------------------
Approach A is *locked* on a vectorized ``compute(panel)`` that sees the whole
``(T×N)`` panel for speed. A hard sandbox (a per-``t`` sliced PanelView that
makes future rows physically unreachable) would defeat that and is explicitly
**out of scope**. So the guard here is **defense-in-depth, not a sandbox**:

1. **Trailing-only helpers** (``rolling``, ``rank``, ``zscore``, ``lag``,
   ``sector_demean``, ``vol_scale``) cover the sanctioned path. By default they
   only read rows ``<= t``; a forward-looking call (``center=True``, negative
   ``lag``, full-sample ``zscore`` with ``as_of=None``) raises
   :class:`LookaheadError`.
2. **Raw array access** (``panel.close[t+1]``, ``panel.close[t:].max()``) is the
   escape hatch a determined author can still abuse. The helper guard *cannot*
   see raw indexing — this is on the **honor system + canary**, caught by the
   future-perturbation dependency detector in ``bt.panel.run.detect_lookahead``
   (and the P2 canary battery), not prevented mechanically. That detector
   scrambles the tail rows of the price/flow/fx fields and asserts ``W[:k]`` is
   invariant; a strategy whose early weights move when the future is perturbed
   is reading the future. See its docstring for the leading-rows coverage limit.
3. Future hardening (not built now, would not change the locked vectorized API):
   a provenance-tracked PanelView that stamps ``as_of`` on every helper output
   and rejects forward transforms that route through helpers.

A future strategy author: raw indexing past ``t`` is on the honor system and is
hunted by the canary battery. Use the trailing-only helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

import numpy as np

from bt.panel.loader import Panel


class LookaheadError(Exception):
    """Raised when a helper is asked to perform a forward-looking transform."""


@dataclass
class WeightMatrix:
    """Target equity fractions ``W[T,N]`` in ``[-1,+1]``; columns map to ``assets``."""

    W: np.ndarray  # [T,N] f64
    assets: list[str]


class PanelView:
    """Read view over an aligned :class:`Panel` with trailing-only helpers.

    Exposes the aligned ``[T,N]`` arrays (``open/high/low/close/volume``,
    ``funding``, ``fx``, ``session``, ``sector``, ``active_mask``) plus the
    canonical ``assets`` order. Helpers are trailing-only by default (see module
    docstring, C4).
    """

    def __init__(self, panel: Panel):
        self._panel = panel

    # ---- aligned arrays -------------------------------------------------
    @property
    def assets(self) -> list[str]:
        return self._panel.assets

    @property
    def timestamp(self) -> np.ndarray:
        return self._panel.timestamp

    @property
    def open(self) -> np.ndarray:
        return self._panel.open

    @property
    def high(self) -> np.ndarray:
        return self._panel.high

    @property
    def low(self) -> np.ndarray:
        return self._panel.low

    @property
    def close(self) -> np.ndarray:
        return self._panel.close

    @property
    def volume(self) -> np.ndarray:
        return self._panel.volume

    @property
    def funding(self) -> np.ndarray:
        return self._panel.funding

    @property
    def fx(self) -> np.ndarray:
        return self._panel.fx

    @property
    def session(self) -> np.ndarray:
        return self._panel.session

    @property
    def sector(self) -> np.ndarray:
        return self._panel.sector

    @property
    def active_mask(self) -> np.ndarray:
        return self._panel.active_mask

    # ---- trailing-only helpers (C4) ------------------------------------
    def lag(self, x: np.ndarray, k: int) -> np.ndarray:
        """Shift ``x`` down by ``k`` rows (row ``t`` gets row ``t-k``).

        ``k >= 0`` only — a negative ``k`` peeks into the future and raises.
        Rows with insufficient history are NaN.
        """
        if k < 0:
            raise LookaheadError(
                f"lag(k={k}) is forward-looking (negative shift peeks into the future)"
            )
        out = np.full_like(x, np.nan, dtype=np.float64)
        if k == 0:
            return np.asarray(x, dtype=np.float64).copy()
        out[k:] = x[:-k]
        return out

    def rolling(
        self, x: np.ndarray, window: int, op: str = "mean", *, center: bool = False
    ) -> np.ndarray:
        """Trailing rolling reduction over ``window`` rows ending at ``t``.

        ``center=True`` would straddle future rows → :class:`LookaheadError`.
        First ``window-1`` rows are NaN (insufficient trailing history).
        ``op`` in {``mean``, ``std``, ``sum``}.
        """
        if center:
            raise LookaheadError(
                "rolling(center=True) straddles future rows — forward-looking"
            )
        if window < 1:
            raise ValueError(f"window must be >= 1, got {window}")
        x = np.asarray(x, dtype=np.float64)
        t, n = x.shape
        out = np.full((t, n), np.nan, dtype=np.float64)
        for ti in range(window - 1, t):
            win = x[ti - window + 1 : ti + 1]
            if op == "mean":
                out[ti] = win.mean(axis=0)
            elif op == "std":
                out[ti] = win.std(axis=0)
            elif op == "sum":
                out[ti] = win.sum(axis=0)
            else:
                raise ValueError(f"unknown op {op!r}")
        return out

    def rank(self, scores: np.ndarray) -> np.ndarray:
        """Per-row cross-sectional rank (0 = lowest). NaN scores rank NaN.

        Operates row-wise (across assets at a fixed ``t``); does not look across
        rows, so it is trivially trailing-safe.
        """
        scores = np.asarray(scores, dtype=np.float64)
        t, n = scores.shape
        out = np.full((t, n), np.nan, dtype=np.float64)
        for ti in range(t):
            row = scores[ti]
            valid = ~np.isnan(row)
            if valid.sum() == 0:
                continue
            order = np.argsort(np.argsort(row[valid]))
            out[ti, valid] = order.astype(np.float64)
        return out

    def zscore(self, x: np.ndarray, *, as_of: str | None = "trailing", window: int | None = None) -> np.ndarray:
        """Z-score along time.

        ``as_of="trailing"`` (default): trailing-window z-score (needs ``window``)
        or expanding z-score using only rows ``<= t``. ``as_of=None`` means a
        full-sample z-score (uses the whole column, including future) and is
        **rejected** — full-sample normalization leaks the future (C4).
        """
        if as_of is None:
            raise LookaheadError(
                "zscore(as_of=None) is a full-sample normalization — uses future "
                "rows; pass as_of='trailing' (optionally with window=...)"
            )
        x = np.asarray(x, dtype=np.float64)
        t, n = x.shape
        out = np.full((t, n), np.nan, dtype=np.float64)
        for ti in range(t):
            lo = 0 if window is None else max(0, ti - window + 1)
            win = x[lo : ti + 1]
            if win.shape[0] < 2:
                continue
            mu = win.mean(axis=0)
            sd = win.std(axis=0)
            sd = np.where(sd == 0.0, np.nan, sd)
            out[ti] = (x[ti] - mu) / sd
        return out

    def sector_demean(self, weights: np.ndarray, sector_row: np.ndarray) -> np.ndarray:
        """Demean ``weights`` (1D, length N) within each sector group.

        Cross-sectional (single row) — no time axis, trailing-safe. Inactive
        (zero-weight) assets stay zero; demeaning is over non-zero legs.
        """
        out = weights.astype(np.float64).copy()
        groups: dict[object, list[int]] = {}
        for j, sec in enumerate(sector_row):
            if weights[j] != 0.0:
                groups.setdefault(sec, []).append(j)
        for idx in groups.values():
            mean = out[idx].mean()
            out[idx] -= mean
        return out

    def vol_scale(self, weights_row: np.ndarray, vol_row: np.ndarray) -> np.ndarray:
        """Inverse-vol scale a single weight row by per-asset trailing vol.

        ``vol_row`` must be a trailing/realized vol estimate (caller supplies it
        via ``rolling(..., op='std')``). Zero/NaN vol → that leg drops out.
        """
        out = weights_row.astype(np.float64).copy()
        inv = np.where((vol_row > 0) & np.isfinite(vol_row), 1.0 / vol_row, 0.0)
        return out * inv


@runtime_checkable
class PanelStrategy(Protocol):
    """A vectorized multi-asset strategy.

    ``compute`` receives the full :class:`PanelView` and returns a
    :class:`WeightMatrix` ``W[T,N]`` of target equity fractions, signed, in
    ``[-1,+1]``, with ``Σ_a |W[t,a]| <= leverage_cap``. Causality is the
    author's responsibility (C4): ``W[t]`` may only depend on rows ``<= t``.
    """

    def compute(self, panel: PanelView) -> WeightMatrix: ...


# Type alias for a strategy-like callable (used by the canary detector).
StrategyFn = Callable[[PanelView], WeightMatrix]
