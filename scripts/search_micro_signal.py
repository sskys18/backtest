#!/usr/bin/env python
"""Short-horizon cross-sectional ORDER-FLOW signal search on Binance perp.

Signals from microstructure (taker buy/sell ratio, open-interest momentum), NOT
price patterns. Per 5m bar pick the strongest firing alt (top_k=1), ride a
close-trailing stop. Overfit control: rolling walk-forward (CSCV PBO) AND a
single-shot untouched-holdout (tune IS 70% -> freeze 1 -> eval OOS 30%).

OBJECTIVE: does order-flow give a short-horizon edge that price-surge did not?
Pre-registered edge iff single-shot OOS net > 0 AND DSR > ~0.95 AND PBO < 0.5.
Else trustworthy negative.
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl

from bt.panel.loader import load_panel
from bt.panel.metrics import panel_metrics
from bt.panel.run import _run_weights
from bt.panel.stats import deflated_sharpe_ratio, pbo as cscv_pbo
from bt.optimize.bayesian import BayesianOptimizer
from bt.panel.walkforward import _slice_panel, make_folds

INTERVAL = "1h"  # free taker/OI depth: 5m→1.7d, 15m→5.2d, 1h→20.8d
PANEL = f"data/raw/binance_micro_panel_{INTERVAL}.parquet"
BTC = f"data/raw/binance_micro_btc_{INTERVAL}.parquet"
PPY = 24 * 365.0
SEED = 42
GAP = 8  # purge/embargo bars (~max_hold)

SEARCH_SPACE = {
    "signal": {"values": ["taker", "oi_mom", "combo"]},
    "lookback": {"min": 1, "max": 12, "step": 1},             # 1h..12h
    "taker_thresh": {"min": 1.0, "max": 2.5, "step": 0.25},   # buySellRatio >1 = net buy
    "oi_thresh": {"min": 0.0, "max": 0.05, "step": 0.01},     # dOI frac over lookback
    "trail_pct": {"min": 0.01, "max": 0.05, "step": 0.01},
    "max_hold": {"min": 1, "max": 8, "step": 1},              # hold 1..8h
}


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    if k < x.shape[0]:
        out[k:] = x[:-k]
    return out


class MicroStrategy:
    """Long the strongest firing alt by order-flow; close-trailing stop. top_k=1."""

    def __init__(self, *, signal, lookback, taker_thresh, oi_thresh, trail_pct, max_hold):
        self.signal = signal
        self.lookback = int(lookback)
        self.taker_thresh = float(taker_thresh)
        self.oi_thresh = float(oi_thresh)
        self.trail_pct = float(trail_pct)
        self.max_hold = int(max_hold)

    def compute(self, close, taker, oi, active) -> np.ndarray:
        t, n = close.shape
        W = np.zeros((t, n), dtype=np.float64)
        d_oi = oi / _lag(oi, self.lookback) - 1.0
        px_up = (close / _lag(close, self.lookback) - 1.0) > 0

        if self.signal == "taker":
            fire = taker > self.taker_thresh
            score = taker
        elif self.signal == "oi_mom":
            fire = (d_oi > self.oi_thresh) & px_up
            score = d_oi
        else:  # combo: aggressive buying + rising OI
            fire = (taker > self.taker_thresh) & (d_oi > self.oi_thresh)
            score = taker
        fire = fire & active & np.isfinite(score) & np.isfinite(d_oi)

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
            if cur < 0:
                row = fire[ti]
                if row.any():
                    j = int(np.argmax(np.where(row, score[ti], -np.inf)))
                    cur, peak, held = j, close[ti, j], 1
                    W[ti, j] = 1.0
        return W


def load_micro(panel):
    """Pivot taker_ratio / oi_usd to [T,N] aligned to panel.assets x panel.timestamp."""
    df = pl.read_parquet(PANEL)
    a_idx = {a: j for j, a in enumerate(panel.assets)}
    t_idx = {int(t): i for i, t in enumerate(panel.timestamp)}
    T, N = panel.timestamp.size, len(panel.assets)
    taker = np.full((T, N), np.nan)
    oi = np.full((T, N), np.nan)
    for r in df.iter_rows(named=True):
        i = t_idx.get(int(r["timestamp"]))
        j = a_idx.get(r["asset"])
        if i is None or j is None:
            continue
        taker[i, j] = r["taker_ratio"]
        oi[i, j] = r["oi_usd"]
    return taker, oi


RUN_CFG = dict(
    initial_collateral=1e6, fee_rate=0.0004, slippage_bps=2.0,  # perp taker ~4bps
    im_rate=1.0, mm_rate=0.5, leverage_cap=1.0, max_net=1.0, max_per_asset=1.0,
    rebalance_freq=1, fill_model="next_bar", on_violation="renormalize",
    cap_kind="hard", numeraire_is_unit_fx=True, session_aware=False,
)


def seg_run(panel, taker, oi, idx, params, cfg):
    p = _slice_panel(panel, idx)
    W = MicroStrategy(**params).compute(p.close, taker[idx], oi[idx], p.active_mask)
    r = _run_weights(p, W, cfg)
    eq = np.asarray(r["equity"])
    ret = np.diff(eq) / eq[:-1] if eq.size > 1 else np.empty(0)
    held = (np.abs(W).sum(1) > 0).astype(int)
    trades = int(((held[1:] - held[:-1]) == 1).sum()) + int(held[0]) if held.size else 0
    return eq, ret, trades


def seg_sharpe(panel, taker, oi, idx, params, cfg):
    p = _slice_panel(panel, idx)
    W = MicroStrategy(**params).compute(p.close, taker[idx], oi[idx], p.active_mask)
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
    taker, oi = load_micro(panel)
    T = panel.timestamp.size
    print(f"panel T={T} N={len(panel.assets)} | taker cov={np.isfinite(taker).mean():.2f} "
          f"oi cov={np.isfinite(oi).mean():.2f}", flush=True)

    # ---------- single-shot untouched OOS (headline) ----------
    cut = int(T * 0.70)
    is_idx, oos_idx = np.arange(0, cut), np.arange(cut, T)
    opt = BayesianOptimizer(seed=SEED)
    res = opt.optimize(lambda pr: seg_sharpe(panel, taker, oi, is_idx, pr, RUN_CFG),
                       SEARCH_SPACE, 80)
    best = res["best_config"]
    distinct = {tuple(sorted(h["config"].items())): h["score"]
                for h in res["history"] if h.get("score") is not None}
    eqn, oret_n, tr_n = seg_run(panel, taker, oi, oos_idx, best, RUN_CFG)
    gross_cfg = {**RUN_CFG, "fee_rate": 0.0, "slippage_bps": 0.0}
    eqg, _, _ = seg_run(panel, taker, oi, oos_idx, best, gross_cfg)
    dsr = float(deflated_sharpe_ratio(oret_n, [s / math.sqrt(PPY) for s in distinct.values()])) if oret_n.size >= 2 else None

    btc = pl.read_parquet(BTC).sort("timestamp")
    bm = dict(zip(btc["timestamp"].to_list(), btc["close"].to_list()))
    bc = np.array([bm.get(int(t), np.nan) for t in panel.timestamp[oos_idx]])
    for i in range(1, len(bc)):
        if np.isnan(bc[i]):
            bc[i] = bc[i - 1]

    print("\n" + "#" * 64)
    print("MICROSTRUCTURE SINGLE-SHOT OOS (tune IS70% -> freeze -> eval OOS30%)")
    print("#" * 64)
    print(f"IS-tuned best: {best}  | IS sharpe={res['best_score']:.3f} trials={len(distinct)}")
    print(f"OOS NET   cum={eqn[-1]/eqn[0]-1:+.4f}  trades={tr_n}")
    print(f"OOS GROSS cum={eqg[-1]/eqg[0]-1:+.4f}")
    print(f"BTC OOS   cum={bc[-1]/bc[0]-1:+.4f}")
    print(f"OOS DSR (deflated by {len(distinct)} trials)={dsr}")

    # ---------- rolling walk-forward (supporting: PBO) ----------
    N_FOLDS = 4
    folds = make_folds(panel.timestamp, scheme="rolling", n_folds=N_FOLDS,
                       embargo_bars=GAP, purge_bars=GAP)
    per_fold_oos, all_distinct = [], {}
    for f in folds:
        r = opt.optimize(lambda pr, fi=f: seg_sharpe(panel, taker, oi, fi.train_idx, pr, RUN_CFG),
                         SEARCH_SPACE, 25)
        for h in r["history"]:
            if h.get("score") is not None:
                all_distinct[tuple(sorted(h["config"].items()))] = h["score"]
        _, oret, _ = seg_run(panel, taker, oi, f.test_idx, r["best_config"], RUN_CFG)
        per_fold_oos.append((f, r["best_config"], oret))
    cfgs = [dict(k) for k in all_distinct]
    cols = []
    for cfgp in cfgs:
        per = [seg_run(panel, taker, oi, f.test_idx, cfgp, RUN_CFG)[1] for f in folds]
        cols.append(np.concatenate(per))
    perf = np.column_stack(cols) if cols else np.empty((0, 0))
    pbo = float(cscv_pbo(perf)) if perf.ndim == 2 and perf.shape[1] >= 2 and perf.shape[0] >= 2 else None
    fold_sh = []
    for f, cfgp, oret in per_fold_oos:
        sd = oret.std(ddof=1) if oret.size > 1 else 0
        fold_sh.append(round(float(oret.mean()/sd*math.sqrt(PPY)) if sd > 0 else 0.0, 2))
    print(f"\nwalk-forward per-fold OOS sharpe={fold_sh}  PBO={pbo}  configs={len(cfgs)}")

    print("\n" + "=" * 64)
    print("EDGE iff single-shot OOS NET>0 AND DSR>~0.95 AND PBO<0.5. Else negative.")
    print("=" * 64)


if __name__ == "__main__":
    main()
