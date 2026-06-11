#!/usr/bin/env python
"""Backtest the heat-screener's entry logic: buy a coin breaking to a new N-bar
high on a volume surge, ride a trailing stop. Tests whether 'enter when hot'
(the screener's fresh-up filter) is actually profitable, with overfit control.

Data: 5m 60d panel (155 liquid alts) — deepest intraday with volume (OI-based
screener piece can't be backtested: free OI history caps at 21d).

Controls: single-shot untouched OOS (tune IS70% -> freeze -> eval OOS30%) +
shuffle placebo (destroy the signal in time; if returns survive, it's mechanic).
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl

from bt.panel.loader import load_panel
from bt.panel.metrics import panel_metrics
from bt.panel.run import _run_weights
from bt.panel.stats import deflated_sharpe_ratio
from bt.optimize.bayesian import BayesianOptimizer
from bt.panel.walkforward import _slice_panel

PANEL = "data/raw/binance_spot_panel_5m.parquet"
BTC = "data/raw/binance_spot_btc_5m.parquet"
PPY = 288 * 365.0
SEED = 42

SPACE = {
    "hi_lookback": {"min": 12, "max": 144, "step": 12},   # new high over 1h..12h
    "vol_lookback": {"min": 6, "max": 48, "step": 6},      # vol baseline window
    "vol_mult": {"min": 1.5, "max": 5.0, "step": 0.5},     # volume surge multiple
    "trail_pct": {"min": 0.01, "max": 0.05, "step": 0.01},
    "max_hold": {"min": 6, "max": 72, "step": 6},
}


def _lag(x, k):
    out = np.full_like(x, np.nan, dtype=np.float64)
    if k < x.shape[0]:
        out[k:] = x[:-k]
    return out


def _roll_max(x, w):
    t, n = x.shape
    out = np.full((t, n), np.nan)
    for i in range(w, t):
        out[i] = np.nanmax(x[i - w:i], axis=0)  # trailing high EXCLUDING bar i
    return out


def _roll_mean(x, w):
    t, n = x.shape
    out = np.full((t, n), np.nan)
    c = np.cumsum(np.nan_to_num(x), axis=0)
    cnt = np.cumsum(np.isfinite(x), axis=0)
    for i in range(w, t):
        s = c[i - 1] - (c[i - w - 1] if i - w - 1 >= 0 else 0)
        k = cnt[i - 1] - (cnt[i - w - 1] if i - w - 1 >= 0 else 0)
        out[i] = np.where(k > 0, s / np.maximum(k, 1), np.nan)
    return out


class BreakoutStrategy:
    """Screener logic: long the strongest breakout (new high + volume surge),
    ranked by volume surge, top_k=1, close-trailing stop."""

    def __init__(self, *, hi_lookback, vol_lookback, vol_mult, trail_pct, max_hold):
        self.hi_lookback = int(hi_lookback)
        self.vol_lookback = int(vol_lookback)
        self.vol_mult = float(vol_mult)
        self.trail_pct = float(trail_pct)
        self.max_hold = int(max_hold)

    def compute(self, close, volume, active):
        t, n = close.shape
        W = np.zeros((t, n))
        prior_hi = _roll_max(close, self.hi_lookback)
        vbase = _roll_mean(volume, self.vol_lookback)
        vratio = np.where(vbase > 0, volume / vbase, 0.0)
        fire = (close >= prior_hi) & (vratio >= self.vol_mult) & active & np.isfinite(prior_hi)
        score = vratio
        cur, peak, held = -1, 0.0, 0
        for ti in range(t):
            if cur >= 0:
                c = close[ti, cur]
                ok = active[ti, cur] and np.isfinite(c)
                if ok:
                    peak = max(peak, c)
                if (not ok) or (ok and c < peak * (1 - self.trail_pct)) or held >= self.max_hold:
                    cur = -1
                else:
                    W[ti, cur] = 1.0
                    held += 1
            if cur < 0 and fire[ti].any():
                j = int(np.argmax(np.where(fire[ti], score[ti], -np.inf)))
                cur, peak, held = j, close[ti, j], 1
                W[ti, j] = 1.0
        return W


CFG = dict(initial_collateral=1e6, fee_rate=0.001, slippage_bps=5.0, im_rate=1.0,
           mm_rate=0.5, leverage_cap=1.0, max_net=1.0, max_per_asset=1.0,
           rebalance_freq=1, fill_model="next_bar", on_violation="renormalize",
           cap_kind="hard", numeraire_is_unit_fx=True, session_aware=False)


def run(panel, vol, idx, params, cfg):
    p = _slice_panel(panel, idx)
    W = BreakoutStrategy(**params).compute(p.close, vol[idx], p.active_mask)
    r = _run_weights(p, W, cfg)
    eq = np.asarray(r["equity"])
    ret = np.diff(eq) / eq[:-1] if eq.size > 1 else np.empty(0)
    held = (np.abs(W).sum(1) > 0).astype(int)
    trades = int(((held[1:] - held[:-1]) == 1).sum()) + int(held[0]) if held.size else 0
    return eq, ret, trades


def sharpe_is(panel, vol, idx, params, cfg):
    p = _slice_panel(panel, idx)
    W = BreakoutStrategy(**params).compute(p.close, vol[idx], p.active_mask)
    try:
        m = panel_metrics(_run_weights(p, W, cfg), timestamps=p.timestamp, close=p.close,
                          fx=p.fx, assets=p.assets, session_aware=False, periods_per_year=PPY)
        v = m["sharpe"]
        return float(v) if np.isfinite(v) else -1e18
    except Exception:
        return -1e18


def main():
    panel = load_panel(PANEL, bar_freq="5m", session_aware=False, numeraire="USD",
                       funding=False, calendar="trading_days")
    vol = panel.volume
    T = panel.timestamp.size
    print(f"panel T={T} N={len(panel.assets)}", flush=True)

    cut = int(T * 0.70)
    is_idx, oos_idx = np.arange(0, cut), np.arange(cut, T)
    opt = BayesianOptimizer(seed=SEED)
    res = opt.optimize(lambda pr: sharpe_is(panel, vol, is_idx, pr, CFG), SPACE, 60)
    best = res["best_config"]
    distinct = {tuple(sorted(h["config"].items())): h["score"]
                for h in res["history"] if h.get("score") is not None}
    eqn, oret, trn = run(panel, vol, oos_idx, best, CFG)
    eqg, _, _ = run(panel, vol, oos_idx, best, {**CFG, "fee_rate": 0.0, "slippage_bps": 0.0})
    dsr = float(deflated_sharpe_ratio(oret, [s / math.sqrt(PPY) for s in distinct.values()])) if oret.size >= 2 else None

    btc = pl.read_parquet(BTC).sort("timestamp")
    bm = dict(zip(btc["timestamp"].to_list(), btc["close"].to_list()))
    bc = np.array([bm.get(int(t), np.nan) for t in panel.timestamp[oos_idx]])
    for i in range(1, len(bc)):
        if np.isnan(bc[i]):
            bc[i] = bc[i - 1]

    print("\n" + "#" * 60)
    print("SCREENER-LOGIC BACKTEST (new-high breakout + volume surge)")
    print("#" * 60)
    print(f"IS-tuned best: {best}")
    print(f"IS sharpe={res['best_score']:.3f} trials={len(distinct)}")
    print(f"OOS NET   cum={eqn[-1]/eqn[0]-1:+.4f} trades={trn}")
    print(f"OOS GROSS cum={eqg[-1]/eqg[0]-1:+.4f}")
    print(f"BTC OOS   cum={bc[-1]/bc[0]-1:+.4f}")
    print(f"OOS DSR (deflated by {len(distinct)} trials)={dsr}")

    # placebo: shuffle volume in time per coin -> destroys 'surge' signal
    rng = np.random.default_rng(0)
    vol_sh = vol.copy()
    for j in range(vol_sh.shape[1]):
        vol_sh[:, j] = vol[rng.permutation(vol.shape[0]), j]
    eqp, _, trp = run(panel, vol_sh, oos_idx, best, CFG)
    print(f"\nPLACEBO (volume shuffled) OOS NET cum={eqp[-1]/eqp[0]-1:+.4f} trades={trp}")
    print("=> if placebo ~ real, the signal adds nothing (mechanic+luck).")
    print("\nEDGE iff OOS NET>0 AND DSR>~0.95 AND real >> placebo. Else negative.")


if __name__ == "__main__":
    main()
