#!/usr/bin/env python
"""Funding-carry tests on Binance USDT perps. Two structurally-different-from-
price-prediction hypotheses (risk premium, not signal mining):

  A. Cross-sectional funding portfolio: daily, dollar-neutral, LONG the
     bottom-quantile trailing-funding coins / SHORT the top-quantile.
     Collects funding on both legs; pays price-return risk + fees.
  B. Delta-neutral carry (long spot / short perp): per-coin funding stream
     measurement — annualized carry, vol, drawdown, per-year regime breakdown.

Rigor (mandatory per experiment protocol):
  - single-shot OOS: params chosen on IS (first 70%) only, one OOS evaluation
  - shuffle placebo: within-day cross-sectional permutation of the signal,
    200 reps, real OOS Sharpe must beat the placebo distribution
  - DSR (deflated by the full IS trial set) + CSCV PBO across the grid

Timing (no lookahead): signal at day t uses funding through day t (last event
16:00 UTC < close). Position held over day t+1: earns close_t→close_{t+1}
price return + day t+1 funding. Liquidity mask is trailing 7d median quote_vol.

Usage:
  python scripts/backtest_funding.py            # cross-sectional A + carry B
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bt.panel.stats import deflated_sharpe_ratio, pbo as cscv_pbo  # noqa: E402

FUNDING_PQ = Path("data/raw/binance_funding_panel.parquet")
PERP_PQ = Path("data/raw/binance_perp_panel_1d.parquet")
PPY = 365.0
FEE = 0.0007  # per side per unit turnover: 5bp taker + 2bp slippage
IS_FRAC = 0.70
N_PLACEBO = 200
GRID_W = [3, 7, 14, 30]      # trailing funding window (days)
GRID_Q = [0.10, 0.20]        # quantile per leg
GRID_LIQ = [5e6, 20e6]       # trailing 7d median quote-vol floor (USD)
SEED = 20260611


def load() -> pl.DataFrame:
    """Daily joined panel: timestamp(day), asset, close, quote_vol, funding(day sum)."""
    f = pl.read_parquet(FUNDING_PQ).with_columns(
        (pl.col("timestamp") // 86_400_000).alias("day")
    )
    fday = f.group_by(["day", "asset"]).agg(pl.col("funding_rate").sum().alias("funding"))

    k = pl.read_parquet(PERP_PQ).with_columns(
        (pl.col("timestamp") // 86_400_000).alias("day")
    )
    k = k.sort(["asset", "day"]).with_columns(
        pl.col("quote_vol")
        .rolling_median(window_size=7, min_periods=7)
        .over("asset")
        .alias("liq_med"),
        (pl.col("close") / pl.col("close").shift(1).over("asset") - 1.0).alias("ret"),
    )
    df = k.join(fday, on=["day", "asset"], how="left").with_columns(
        pl.col("funding").fill_null(0.0)
    )
    return df.sort(["asset", "day"])


def build_arrays(df: pl.DataFrame, w: int, liq_floor: float):
    """Wide arrays aligned on day index: ret, funding, signal, eligible mask."""
    df = df.with_columns(
        pl.col("funding")
        .rolling_mean(window_size=w, min_periods=w)
        .over("asset")
        .alias("sig")
    )
    days = df["day"].unique().sort()
    assets = df["asset"].unique().sort()
    di = {d: i for i, d in enumerate(days)}
    ai = {a: i for i, a in enumerate(assets)}
    T, N = len(days), len(assets)
    ret = np.full((T, N), np.nan)
    fund = np.full((T, N), np.nan)
    sig = np.full((T, N), np.nan)
    liq = np.full((T, N), np.nan)
    rows = df.select(["day", "asset", "ret", "funding", "sig", "liq_med"]).rows()
    for d, a, r, fu, s, l in rows:
        i, j = di[d], ai[a]
        if r is not None:
            ret[i, j] = r
        fund[i, j] = fu
        if s is not None:
            sig[i, j] = s
        if l is not None:
            liq[i, j] = l
    elig = ~np.isnan(sig) & ~np.isnan(ret) & (np.nan_to_num(liq, nan=0.0) >= liq_floor)
    return ret, fund, sig, elig, days


def run_strategy(ret, fund, sig, elig, q: float, rng=None):
    """Daily per-bar net returns of the dollar-neutral funding portfolio.

    Signal/eligibility at day t → weights held over day t+1.
    If rng given, signal is permuted within-day across eligible assets (placebo).
    """
    T, N = ret.shape
    pnl = np.zeros(T)
    w_prev = np.zeros(N)
    for t in range(T - 1):
        idx = np.where(elig[t])[0]
        w = np.zeros(N)
        if idx.size >= 10:
            s = sig[t, idx]
            if rng is not None:
                s = rng.permutation(s)
            k = max(1, int(round(q * idx.size)))
            order = np.argsort(s)
            lo, hi = idx[order[:k]], idx[order[-k:]]
            w[lo] = +0.5 / k   # long low-funding (receives when negative)
            w[hi] = -0.5 / k   # short high-funding (receives when positive)
        r1 = np.nan_to_num(ret[t + 1], nan=0.0)
        f1 = np.nan_to_num(fund[t + 1], nan=0.0)
        turnover = np.abs(w - w_prev).sum()
        pnl[t + 1] = float(w @ r1 - w @ f1 - turnover * FEE)
        w_prev = w
    return pnl


def ann_sharpe(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if x.size < 2 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / x.std(ddof=1) * math.sqrt(PPY))


def max_dd(x: np.ndarray) -> float:
    eq = np.cumsum(x)
    return float((eq - np.maximum.accumulate(eq)).min())


def cross_sectional_test(df: pl.DataFrame) -> None:
    print("=" * 72)
    print("A. CROSS-SECTIONAL FUNDING (long low / short high, dollar-neutral)")
    print("=" * 72)

    all_days = df["day"].unique().sort()
    split = all_days[int(len(all_days) * IS_FRAC)]
    print(f"days={len(all_days)}  IS/OOS split day={split} "
          f"(IS {all_days[0]}..{split-1}, OOS {split}..{all_days[-1]})")

    # --- IS grid search ---
    results = []
    pnl_cols = []
    for liq_floor in GRID_LIQ:
        for w in GRID_W:
            ret, fund, sig, elig, days = build_arrays(df, w, liq_floor)
            d = days.to_numpy()
            is_mask = d < split
            for q in GRID_Q:
                pnl = run_strategy(ret, fund, sig, elig, q)
                s_is = ann_sharpe(pnl[is_mask])
                results.append({"w": w, "q": q, "liq": liq_floor, "sr_is": s_is,
                                "pnl": pnl, "days": d})
                pnl_cols.append(pnl)
                print(f"  W={w:>2} q={q:.2f} liq={liq_floor/1e6:>4.0f}M  "
                      f"IS Sharpe={s_is:+.2f}  IS ann.ret={pnl[is_mask].mean()*PPY:+.1%}")

    # PBO across the whole grid (CSCV, full-sample columns)
    L = min(len(p) for p in pnl_cols)
    M = np.stack([p[:L] for p in pnl_cols], axis=1)
    pbo_val = cscv_pbo(M, n_splits=16)
    print(f"\nCSCV PBO over {len(pnl_cols)} configs: {pbo_val:.2f}")

    # --- single-shot OOS on IS-best ---
    best = max(results, key=lambda r: r["sr_is"])
    d = best["days"]
    oos_mask = d >= split
    oos = best["pnl"][oos_mask]
    is_trial_sharpes = [r["sr_is"] / math.sqrt(PPY) for r in results]
    dsr = float(deflated_sharpe_ratio(oos, is_trial_sharpes)) if oos.size >= 2 else None
    print(f"\nIS-best: W={best['w']} q={best['q']} liq={best['liq']/1e6:.0f}M "
          f"(IS Sharpe {best['sr_is']:+.2f})")
    print(f"OOS: Sharpe={ann_sharpe(oos):+.2f}  ann.ret={oos.mean()*PPY:+.1%}  "
          f"maxDD={max_dd(oos):+.1%}  days={oos.size}")
    print(f"OOS DSR (deflated by {len(results)} trials)={dsr}")

    # decompose OOS: price leg vs funding leg (re-run capturing components)
    ret, fund, sig, elig, days = build_arrays(df, best["w"], best["liq"])
    T, N = ret.shape
    price_leg = np.zeros(T); fund_leg = np.zeros(T); cost_leg = np.zeros(T)
    w_prev = np.zeros(N)
    for t in range(T - 1):
        idx = np.where(elig[t])[0]
        w = np.zeros(N)
        if idx.size >= 10:
            s = sig[t, idx]
            k = max(1, int(round(best["q"] * idx.size)))
            order = np.argsort(s)
            w[idx[order[:k]]] = +0.5 / k
            w[idx[order[-k:]]] = -0.5 / k
        price_leg[t+1] = float(w @ np.nan_to_num(ret[t+1], nan=0.0))
        fund_leg[t+1] = float(-(w @ np.nan_to_num(fund[t+1], nan=0.0)))
        cost_leg[t+1] = -np.abs(w - w_prev).sum() * FEE
        w_prev = w
    om = days.to_numpy() >= split
    print(f"OOS decomposition (ann.): price={price_leg[om].mean()*PPY:+.1%}  "
          f"funding={fund_leg[om].mean()*PPY:+.1%}  costs={cost_leg[om].mean()*PPY:+.1%}")

    # --- shuffle placebo on OOS ---
    rng = np.random.default_rng(SEED)
    beats = 0
    placebo_srs = []
    real_sr = ann_sharpe(oos)
    for _ in range(N_PLACEBO):
        p = run_strategy(ret, fund, sig, elig, best["q"], rng=rng)
        psr = ann_sharpe(p[om])
        placebo_srs.append(psr)
        if real_sr > psr:
            beats += 1
    ps = np.array(placebo_srs)
    print(f"placebo (within-day shuffle, {N_PLACEBO} reps): real OOS SR {real_sr:+.2f} "
          f"beats {beats}/{N_PLACEBO}  placebo mean={ps.mean():+.2f} p95={np.quantile(ps,0.95):+.2f}")


def carry_test(df: pl.DataFrame) -> None:
    print()
    print("=" * 72)
    print("B. DELTA-NEUTRAL CARRY (long spot / short perp, funding stream)")
    print("=" * 72)
    # short-perp leg receives +funding when positive; basis drift ~0 at daily
    # horizon for liquid perps (mark-indexed); entry/exit ~4x5bp amortized.
    liq_now = (
        df.group_by("asset")
        .agg(pl.col("quote_vol").tail(30).median().alias("liq30"))
        .sort("liq30", descending=True)
    )
    top = liq_now.head(20)["asset"].to_list()
    for a in ["BTC-USDT", "ETH-USDT"]:
        if a not in top:
            top.insert(0, a)

    print(f"{'asset':<12} {'days':>5} {'ann.carry':>9} {'SR':>6} {'maxDD':>7} "
          f"{'pos%':>5}  per-year ann.carry")
    for a in top:
        sub = df.filter(pl.col("asset") == a).sort("day")
        fd = sub.group_by("day").agg(pl.col("funding").first()).sort("day")
        x = fd["funding"].to_numpy()
        if x.size < 180:
            continue
        years = {}
        for d, v in zip(fd["day"].to_numpy(), x):
            y = 1970 + int(d // 365.25)
            years.setdefault(y, []).append(v)
        ystr = " ".join(
            f"{y}:{np.mean(v)*PPY:+.0%}" for y, v in sorted(years.items()) if len(v) > 60
        )
        print(f"{a:<12} {x.size:>5} {x.mean()*PPY:>+9.1%} {ann_sharpe(x):>6.2f} "
              f"{max_dd(x):>+7.1%} {(x>0).mean():>5.0%}  {ystr}")

    # equal-weight top-20 carry basket
    basket = df.filter(pl.col("asset").is_in(top[:20]))
    bd = basket.group_by("day").agg(pl.col("funding").mean().alias("f")).sort("day")
    x = bd["f"].to_numpy()
    print(f"\nEW top-20-liq basket: ann.carry={x.mean()*PPY:+.1%}  SR={ann_sharpe(x):.2f}  "
          f"maxDD={max_dd(x):+.1%}  pos%={(x>0).mean():.0%}  days={x.size}")


def main() -> None:
    df = load()
    n_assets = df["asset"].n_unique()
    print(f"loaded: {df.height} rows, {n_assets} assets, "
          f"days {df['day'].min()}..{df['day'].max()}")
    cross_sectional_test(df)
    carry_test(df)


if __name__ == "__main__":
    main()
