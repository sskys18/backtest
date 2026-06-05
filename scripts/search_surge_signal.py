#!/usr/bin/env python
"""Search intraday surge signals: enter the strongest alt, ride a trailing stop a
few hours, exit on stop/max-hold. Find one that beats buy-hold — with REAL
overfit control (walk-forward + DSR deflated by full trial count + CSCV PBO).

OBJECTIVE (state crisply so a wrong guess is correctable):
  Per 5m bar, when a candidate signal FIRES on a liquid alt, go long the strongest
  firing alt (top_k=1, info<=t), ride a CLOSE-resolution trailing stop until the
  stop trips or max_hold elapses. Long-only, net of 10bps fee + 5bps slippage.
  Question: does this have a real edge — OOS Sharpe surviving DSR deflation by the
  search cardinality — AND does it beat BTC buy-hold over the same OOS bars?

KNOWN LIMITS (advisor): trailing stop is CLOSE-resolution (bar granularity), not
intra-bar; fills are next-bar-open; flat slippage is fair ONLY because the panel
is liquid-only ($20M rolling-24h floor). A win here is real-ish; a loss is a
trustworthy negative.
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl

from bt.panel.loader import load_panel
from bt.panel.strategy import PanelView, WeightMatrix
from bt.panel.walkforward import make_folds, walk_forward

PANEL = "data/raw/binance_spot_panel_5m.parquet"
BTC = "data/raw/binance_spot_btc_5m.parquet"
PPY = 288 * 365.0  # 5m bars per year
SEED = 42


class SurgeStrategy:
    """Long the single strongest firing alt; ride a close-resolution trailing
    stop. top_k=1 → a cheap scalar state machine over the time axis."""

    def __init__(self, *, signal, lookback, entry_thresh, vol_mult, trail_pct, max_hold):
        self.signal = signal
        self.lookback = int(lookback)
        self.entry_thresh = float(entry_thresh)
        self.vol_mult = float(vol_mult)
        self.trail_pct = float(trail_pct)
        self.max_hold = int(max_hold)

    def compute(self, panel: PanelView) -> WeightMatrix:
        close = panel.close
        volume = panel.volume
        t, n = close.shape
        W = np.zeros((t, n), dtype=np.float64)

        mom = close / panel.lag(close, self.lookback) - 1.0          # trailing return
        vma = panel.rolling(volume, window=self.lookback, op="mean")
        vratio = np.where(vma > 0, volume / vma, 0.0)

        if self.signal == "momentum":
            fire = mom > self.entry_thresh
        elif self.signal == "volume":
            fire = vratio > self.vol_mult
        else:  # combo
            fire = (mom > self.entry_thresh) & (vratio > self.vol_mult)
        fire = fire & panel.active_mask & np.isfinite(mom)

        cur = -1       # current held coin (-1 = flat)
        peak = 0.0
        held = 0
        for ti in range(t):
            if cur >= 0:
                c = close[ti, cur]
                ok = panel.active_mask[ti, cur] and np.isfinite(c)
                if ok:
                    peak = max(peak, c)
                stop_hit = ok and c < peak * (1.0 - self.trail_pct)
                if (not ok) or stop_hit or held >= self.max_hold:
                    cur = -1  # exit; flat this bar
                else:
                    W[ti, cur] = 1.0
                    held += 1
            if cur < 0:
                row = fire[ti]
                if row.any():
                    scores = np.where(row, mom[ti], -np.inf)
                    j = int(np.argmax(scores))
                    cur = j
                    peak = close[ti, j]
                    held = 1
                    W[ti, j] = 1.0
        return WeightMatrix(W, assets=panel.assets)


SEARCH_SPACE = {
    "signal": {"values": ["momentum", "volume", "combo"]},
    "lookback": {"min": 3, "max": 24, "step": 3},        # 15min..2h on 5m
    "entry_thresh": {"min": 0.005, "max": 0.05, "step": 0.005},
    "vol_mult": {"min": 1.5, "max": 5.0, "step": 0.5},
    "trail_pct": {"min": 0.01, "max": 0.05, "step": 0.01},
    "max_hold": {"min": 6, "max": 72, "step": 6},        # 30min..6h on 5m
}


def factory(params: dict):
    return SurgeStrategy(**params).compute


def single_shot_oos(panel, run_cfg) -> None:
    """Gold-standard test: tune on first 70% (IS), freeze the ONE best config,
    evaluate ONCE on the untouched last 30% (OOS). Avoids the per-fold
    config-mixing of the rolling walk-forward — one interpretable number."""
    from bt.optimize.bayesian import BayesianOptimizer
    from bt.panel.metrics import panel_metrics
    from bt.panel.stats import deflated_sharpe_ratio
    from bt.panel.walkforward import _slice_panel

    T = panel.timestamp.size
    cut = int(T * 0.70)
    is_p = _slice_panel(panel, np.arange(0, cut))
    oos_p = _slice_panel(panel, np.arange(cut, T))

    def score_is(params):
        try:
            W = SurgeStrategy(**params).compute(PanelView(is_p)).W
            m = panel_metrics(_run_weights(is_p, W, run_cfg), timestamps=is_p.timestamp,
                              close=is_p.close, fx=is_p.fx, assets=is_p.assets,
                              session_aware=False, periods_per_year=PPY)
            v = m["sharpe"]
            return float(v) if np.isfinite(v) else -1e18
        except Exception:
            return -1e18

    res = BayesianOptimizer(seed=SEED).optimize(score_is, SEARCH_SPACE, 80)
    best = res["best_config"]
    distinct = {tuple(sorted(h["config"].items())): h["score"]
                for h in res["history"] if h.get("score") is not None}

    def ev(p, cfg):
        W = SurgeStrategy(**best).compute(PanelView(p)).W
        eq = np.asarray(_run_weights(p, W, cfg)["equity"])
        ret = np.diff(eq) / eq[:-1]
        held = (np.abs(W).sum(1) > 0).astype(int)
        trades = int(((held[1:] - held[:-1]) == 1).sum()) + int(held[0])
        return eq[-1] / eq[0] - 1.0, ret, trades

    net_cfg = run_cfg
    gross_cfg = {**run_cfg, "fee_rate": 0.0, "slippage_bps": 0.0}
    net_cum, oos_ret, trades = ev(oos_p, net_cfg)
    gross_cum, _, _ = ev(oos_p, gross_cfg)
    dsr = float(deflated_sharpe_ratio(oos_ret, [s / math.sqrt(PPY) for s in distinct.values()]))

    print("\n" + "#" * 64)
    print("SINGLE-SHOT OOS (tune IS 70% -> freeze 1 -> eval untouched 30%)")
    print("#" * 64)
    print(f"IS-tuned best: {best}")
    print(f"IS train sharpe={res['best_score']:.3f}  trials={len(distinct)}")
    print(f"OOS untouched: NET cum={net_cum:+.4f}  GROSS cum={gross_cum:+.4f}  "
          f"trades={trades}  DSR={dsr:.4f}")
    print("=> NET~=GROSS at few trades => failure is generalization, not fees.")


def metrics_from_returns(r: np.ndarray) -> dict:
    r = np.asarray(r, dtype=np.float64)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return {"sharpe": 0.0, "cum": 0.0, "n": int(r.size)}
    sd = r.std(ddof=1)
    sharpe = float(r.mean() / sd * math.sqrt(PPY)) if sd > 0 else 0.0
    return {"sharpe": sharpe, "cum": float(np.prod(1 + r) - 1.0), "n": int(r.size)}


def main() -> None:
    panel = load_panel(
        PANEL, bar_freq="5m", session_aware=False,
        numeraire="USD", funding=False, calendar="trading_days",
    )
    print(f"panel: T={panel.timestamp.size} bars  N={len(panel.assets)} alts", flush=True)

    run_cfg = {
        "initial_collateral": 1_000_000.0,
        "fee_rate": 0.001, "slippage_bps": 5.0,
        "im_rate": 1.0, "mm_rate": 0.5,
        "leverage_cap": 1.0, "max_net": 1.0, "max_per_asset": 1.0,
        "rebalance_freq": 1, "fill_model": "next_bar",
        "on_violation": "renormalize", "cap_kind": "hard",
        "numeraire_is_unit_fx": True, "session_aware": False,
    }

    N_FOLDS, PURGE, EMBARGO, BUDGET = 4, 72, 72, 30
    wf = walk_forward(
        panel, strategy_factory=factory, search_space=SEARCH_SPACE,
        run_cfg=run_cfg, scheme="rolling", n_folds=N_FOLDS,
        embargo_bars=EMBARGO, purge_bars=PURGE, budget=BUDGET,
        periods_per_year=PPY, session_aware=False, objective_metric="sharpe", seed=SEED,
    )

    from bt.panel.stats import deflated_sharpe_ratio, pbo as cscv_pbo

    oos_ret = np.asarray(wf["oos_returns"], dtype=np.float64)
    trial_sharpes = list(wf["trial_sharpes"])
    dsr = float(deflated_sharpe_ratio(oos_ret, trial_sharpes)) if oos_ret.size >= 2 else None
    perf = np.asarray(wf["oos_perf_matrix"], dtype=np.float64)
    pbo = (float(cscv_pbo(perf))
           if perf.ndim == 2 and perf.shape[1] >= 2 and perf.shape[0] >= 2 else None)

    # BTC buy-hold over the SAME OOS test bars (concat per fold) — beta benchmark.
    folds = make_folds(panel.timestamp, scheme="rolling", n_folds=N_FOLDS,
                       embargo_bars=EMBARGO, purge_bars=PURGE)
    btc = pl.read_parquet(BTC).sort("timestamp")
    bmap = dict(zip(btc["timestamp"].to_list(), btc["close"].to_list()))
    bclose = np.array([bmap.get(int(t), np.nan) for t in panel.timestamp], dtype=np.float64)
    for i in range(1, bclose.size):
        if np.isnan(bclose[i]):
            bclose[i] = bclose[i - 1]
    btc_oos = []
    for f in folds:
        seg = bclose[f.test_idx]
        btc_oos.append(np.diff(seg) / seg[:-1])
    btc_oos = np.concatenate(btc_oos)

    print("\n" + "#" * 64)
    print("SURGE-SIGNAL SEARCH — OOS (overfit-controlled)")
    print("#" * 64)
    print(f"distinct configs tested (trial count): {len(trial_sharpes)}")
    print(f"per-fold OOS sharpe: {[round(x,3) for x in wf['oos_sharpe']]}")
    print(f"OOS-mean sharpe:     {wf['oos_metrics_mean'].get('sharpe'):.4f}")
    s, b = metrics_from_returns(oos_ret), metrics_from_returns(btc_oos)
    print(f"\nstrategy OOS:    sharpe={s['sharpe']:.3f}  cum={s['cum']:+.4f}  bars={s['n']}")
    print(f"BTC OOS (hold):  sharpe={b['sharpe']:.3f}  cum={b['cum']:+.4f}  bars={b['n']}")
    print(f"\nDSR (deflated by {len(trial_sharpes)} trials): {dsr}")
    print(f"PBO (CSCV): {pbo}")
    best = max(wf["folds"], key=lambda fr: fr["oos_metrics"]["sharpe"])
    print(f"\nbest-OOS-fold params: {best['best_params']}")
    print("\n" + "=" * 64)
    print("EDGE iff: DSR > ~0.95 AND strategy OOS sharpe > BTC AND PBO < 0.5.")
    print("Else: trustworthy negative (liquid 5m alts are heavily arbitraged).")
    print("=" * 64)

    # Gold-standard finalizer: one frozen config on a never-touched holdout.
    single_shot_oos(panel, run_cfg)


if __name__ == "__main__":
    main()
