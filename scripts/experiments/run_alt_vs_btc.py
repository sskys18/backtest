#!/usr/bin/env python
"""Run the long-only alt-momentum basket and compare NET vs BTC buy-hold.

Headline comparison is NET-of-fee (strategy pays 10bps + slippage; BTC buy-hold
pays ~0) over (a) the full window and (b) the recent ~90-bar slice ('these days').
Plus the engine's walk-forward OOS Sharpe + real DSR.

Interpretation rule (survivorship): klines serve only live symbols, deleting the
basket's worst trades (anti-momentum bias). Basket LOSES to BTC => trustworthy
negative. Basket BEATS BTC => confounded, not alpha.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import polars as pl

from bt.panel.report import run_panel_backtest

PANEL = "data/raw/binance_spot_panel_1d.parquet"
BTC = "data/raw/binance_spot_btc_1d.parquet"
CFG = "configs/panel/binance_alt_vs_btc_daily.yaml"
PPY = 365.0
RECENT_BARS = 90


def metrics_from_equity(eq: np.ndarray) -> dict:
    eq = np.asarray(eq, dtype=np.float64)
    if eq.size < 2:
        return {"return": 0.0, "sharpe": 0.0, "sortino": 0.0, "max_dd": 0.0}
    r = eq[1:] / eq[:-1] - 1.0
    sd = r.std(ddof=1)
    sharpe = float(r.mean() / sd * math.sqrt(PPY)) if sd > 0 else 0.0
    dn = np.minimum(r, 0.0)
    dd_dev = math.sqrt(float((dn ** 2).mean()))
    sortino = float(r.mean() / dd_dev * math.sqrt(PPY)) if dd_dev > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak) / peak).min())
    return {
        "return": float(eq[-1] / eq[0] - 1.0),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": max_dd,
    }


def fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def main() -> None:
    # Panel timestamps (sorted unique) — aligns with the strategy equity index.
    pts = (
        pl.read_parquet(PANEL, columns=["timestamp"])
        .unique()
        .sort("timestamp")["timestamp"]
        .to_numpy()
    )

    report = run_panel_backtest(PANEL, CFG)
    eq = np.asarray(report["equity"], dtype=np.float64)
    assert eq.size == pts.size, f"equity {eq.size} != timestamps {pts.size}"

    # BTC buy-hold aligned to the SAME panel timestamps.
    btc = pl.read_parquet(BTC).sort("timestamp")
    bmap = dict(zip(btc["timestamp"].to_list(), btc["close"].to_list()))
    bclose = np.array([bmap.get(int(t), np.nan) for t in pts], dtype=np.float64)
    # forward-fill any gap so buy-hold is continuous on the panel grid
    for i in range(1, bclose.size):
        if np.isnan(bclose[i]):
            bclose[i] = bclose[i - 1]
    btc_eq = bclose / bclose[0]

    def block(name, s_eq, b_eq):
        s, b = metrics_from_equity(s_eq), metrics_from_equity(b_eq)
        print(f"\n=== {name} ({fmt(pts[-len(s_eq)])} -> {fmt(pts[-1])}, {len(s_eq)} bars) ===")
        hdr = f"{'metric':<12}{'BASKET(net)':>14}{'BTC buy-hold':>14}{'winner':>10}"
        print(hdr)
        for k in ("return", "sharpe", "sortino", "max_dd"):
            sv, bv = s[k], b[k]
            # max_dd: closer to 0 is better; others: higher is better
            win = "BASKET" if (sv > bv if k != "max_dd" else sv > bv) else "BTC"
            print(f"{k:<12}{sv:>14.4f}{bv:>14.4f}{win:>10}")
        return s, b

    print("#" * 64)
    print("LONG-ONLY ALT MOMENTUM BASKET  vs  BTC BUY-HOLD  (NET of fees)")
    print("#" * 64)
    block("FULL WINDOW", eq, btc_eq)
    block(f"RECENT {RECENT_BARS} BARS ('these days')", eq[-RECENT_BARS:], btc_eq[-RECENT_BARS:])

    m = report["metrics"]
    print(f"\nstrategy engine metrics: turnover_total={m['turnover_total']:.1f} "
          f"hit_rate={m['hit_rate']:.3f} gross_exp_mean={m['gross_exposure_mean']:.3f}")

    wf = report.get("walk_forward")
    if wf:
        print(f"\nwalk-forward OOS: per-fold sharpe={[round(x,3) for x in wf['oos_sharpe']]}")
        print(f"  OOS-mean sharpe={wf['oos_metrics_mean'].get('sharpe'):.4f}  "
              f"DSR={wf.get('dsr')}  PBO={wf.get('pbo')}")

    print("\n" + "=" * 64)
    print("READ: basket NET-loses to BTC => trustworthy negative (survivorship")
    print("tailwind makes a real win hard; a loss despite it is conclusive).")
    print("=" * 64)


if __name__ == "__main__":
    main()
