#!/usr/bin/env python
"""Cross-sectional short-term REVERSAL on Binance perp (the shortable mirror of the
momentum/surge trades that all came back null).

Every bar, rank liquid alts by trailing formation return; LONG the biggest losers,
SHORT the biggest winners (market-neutral), hold `hold` bars. Thesis: intraday
surges mean-revert (KR t-stats -4..-12; 5m surge null) — so fade them. Shortable
on perp (unlike KR small-caps).

Controls: single-shot untouched OOS + shuffle placebo (permute the signal ACROSS
coins per bar -> random selection, same mechanic) + 5-fold walk-forward.
Market-neutral => benchmark is 0 (not BTC). Edge iff OOS net>0 AND DSR>~0.95 AND
real >> placebo across folds.
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
from bt.panel.walkforward import _slice_panel, make_folds

PANEL = "data/raw/binance_spot_panel_5m.parquet"
PPY = 288 * 365.0
SEED = 42

SPACE = {
    "lookback": {"min": 3, "max": 72, "step": 3},   # formation 15m..6h
    "k": {"min": 1, "max": 10, "step": 1},           # n longs = n shorts
    "hold": {"min": 1, "max": 36, "step": 3},        # rebalance every `hold` bars
}


def _lag(x, k):
    out = np.full_like(x, np.nan, dtype=np.float64)
    if k < x.shape[0]:
        out[k:] = x[:-k]
    return out


def reversal_W(close, active, lookback, k, hold, shuffle_rng=None):
    t, n = close.shape
    ret = close / _lag(close, lookback) - 1.0
    W = np.zeros((t, n))
    last = None
    for ti in range(t):
        if ti % hold != 0 and last is not None:
            # carry weights forward, but drop coins now inactive (engine rejects
            # weight on inactive/NaN cells); renormalize gross to keep ~constant.
            w = last.copy()
            w[~active[ti]] = 0.0
            g = np.abs(w).sum()
            W[ti] = w / g if g > 0 else w
            continue
        row = ret[ti].copy()
        valid = active[ti] & np.isfinite(row)
        idx = np.where(valid)[0]
        if idx.size < 2 * k:
            last = np.zeros(n)
            continue
        score = row[idx]
        if shuffle_rng is not None:           # placebo: random selection, same mechanic
            score = score[shuffle_rng.permutation(score.size)]
        order = idx[np.argsort(score)]        # ascending return
        losers = order[:k]                    # biggest losers -> LONG (reversal)
        winners = order[-k:]                  # biggest winners -> SHORT
        w = np.zeros(n)
        w[losers] = +1.0 / k
        w[winners] = -1.0 / k
        g = np.abs(w).sum()
        if g > 0:
            w = w / g                         # gross=1, net~0
        W[ti] = w
        last = w
    return W


CFG = dict(initial_collateral=1e6, fee_rate=0.0004, slippage_bps=2.0, im_rate=1.0,
           mm_rate=0.5, leverage_cap=1.0, max_net=1.0, max_per_asset=1.0,
           rebalance_freq=1, fill_model="next_bar", on_violation="renormalize",
           cap_kind="hard", numeraire_is_unit_fx=True, session_aware=False)


def run(panel, idx, params, cfg, rng=None):
    p = _slice_panel(panel, idx)
    W = reversal_W(p.close, p.active_mask, params["lookback"], params["k"], params["hold"], rng)
    r = _run_weights(p, W, cfg)
    eq = np.asarray(r["equity"])
    ret = np.diff(eq) / eq[:-1] if eq.size > 1 else np.empty(0)
    return eq, ret


def sharpe_is(panel, idx, params, cfg):
    eq, ret = run(panel, idx, params, cfg)
    if ret.size < 2:
        return -1e18
    sd = ret.std(ddof=1)
    return float(ret.mean() / sd * math.sqrt(PPY)) if sd > 0 else -1e18


def main():
    panel = load_panel(PANEL, bar_freq="5m", session_aware=False, numeraire="USD",
                       funding=False, calendar="trading_days")
    T = panel.timestamp.size
    print(f"panel T={T} N={len(panel.assets)}", flush=True)
    opt = BayesianOptimizer(seed=SEED)

    # single-shot untouched OOS
    cut = int(T * 0.70)
    is_idx, oos_idx = np.arange(cut), np.arange(cut, T)
    res = opt.optimize(lambda pr: sharpe_is(panel, is_idx, pr, CFG), SPACE, 60)
    best = res["best_config"]
    dist = {tuple(sorted(h["config"].items())): h["score"]
            for h in res["history"] if h.get("score") is not None}
    eqn, oret = run(panel, oos_idx, best, CFG)
    eqp, _ = run(panel, oos_idx, best, CFG, rng=np.random.default_rng(0))
    dsr = float(deflated_sharpe_ratio(oret, [s / math.sqrt(PPY) for s in dist.values()])) if oret.size >= 2 else None
    sd = oret.std(ddof=1)
    oos_sh = oret.mean() / sd * math.sqrt(PPY) if sd > 0 else 0.0
    print("\n" + "#" * 60)
    print("CROSS-SECTIONAL REVERSAL (long losers / short winners, perp)")
    print("#" * 60)
    print(f"IS-tuned best: {best}  IS sharpe={res['best_score']:.3f} trials={len(dist)}")
    print(f"OOS NET cum={eqn[-1]/eqn[0]-1:+.4f} sharpe={oos_sh:.3f}")
    print(f"PLACEBO  cum={eqp[-1]/eqp[0]-1:+.4f}  (random selection, same mechanic)")
    print(f"OOS DSR (deflated by {len(dist)} trials)={dsr}")

    # 5-fold walk-forward + per-fold placebo
    folds = make_folds(panel.timestamp, scheme="rolling", n_folds=5, embargo_bars=72, purge_bars=72)
    reals = []
    print(f"\n{'fold':5}{'real':>10}{'placebo':>10}")
    for f in folds:
        r = opt.optimize(lambda pr, fi=f: sharpe_is(panel, fi.train_idx, pr, CFG), SPACE, 30)
        eqr, _ = run(panel, f.test_idx, r["best_config"], CFG)
        eqpl, _ = run(panel, f.test_idx, r["best_config"], CFG, rng=np.random.default_rng(1))
        rn = eqr[-1] / eqr[0] - 1
        reals.append(rn)
        print(f"{f.index:<5}{rn:>+10.3f}{eqpl[-1]/eqpl[0]-1:>+10.3f}")
    print(f"\nfolds real>0: {sum(1 for x in reals if x>0)}/5  median={np.median(reals):+.3f}")
    print("EDGE iff OOS net>0 AND DSR>~0.95 AND real>>placebo across folds.")


if __name__ == "__main__":
    main()
