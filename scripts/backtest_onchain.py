#!/usr/bin/env python
"""Cross-sectional on-chain fundamentals test on Binance USDT perps.

Per-coin on-chain data (CoinMetrics community, 33-asset overlap) — the
fundamental-data family, structurally different from price/flow (proven null)
and funding (compressed). Three hypotheses with FIXED directions (no sign
mining):

  mvrv  : valuation mean-reversion — LONG low MVRV / SHORT high MVRV
  adrg  : network-growth momentum  — LONG high active-addr growth / SHORT low
  nvt   : value (cap per transfer) — LONG low NVT / SHORT high NVT

Engine identical to backtest_funding.py: daily dollar-neutral quantile
portfolio, PnL = price return − funding paid − turnover fees. On-chain signal
LAGGED 2 DAYS (publication lag, conservative).

Rigor: IS 70 % grid (8 configs) → single-shot OOS, within-day shuffle placebo
×200, DSR deflated by all trials, CSCV PBO. Plus signal-vs-momentum IC to
check the signal isn't momentum in disguise.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bt.panel.stats import deflated_sharpe_ratio, pbo as cscv_pbo  # noqa: E402

ONCHAIN_PQ = Path("data/raw/coinmetrics_onchain_1d.parquet")
PERP_PQ = Path("data/raw/binance_perp_panel_1d.parquet")
FUNDING_PQ = Path("data/raw/binance_funding_panel.parquet")
PPY = 365.0
FEE = 0.0007
LAG = 2          # on-chain publication lag (days)
LIQ_FLOOR = 5e6
IS_FRAC = 0.70
N_PLACEBO = 200
MIN_XS = 10      # min eligible assets per day
SEED = 20260611


def load() -> pl.DataFrame:
    oc = pl.read_parquet(ONCHAIN_PQ).with_columns(
        (pl.col("date").str.to_date().cast(pl.Int32)).alias("day")
    )
    k = pl.read_parquet(PERP_PQ).with_columns(
        (pl.col("timestamp") // 86_400_000).cast(pl.Int32).alias("day")
    )
    k = k.sort(["asset", "day"]).with_columns(
        pl.col("quote_vol").rolling_median(window_size=7, min_samples=7)
        .over("asset").alias("liq_med"),
        (pl.col("close") / pl.col("close").shift(1).over("asset") - 1.0).alias("ret"),
    )
    f = pl.read_parquet(FUNDING_PQ).with_columns(
        (pl.col("timestamp") // 86_400_000).cast(pl.Int32).alias("day")
    )
    fday = f.group_by(["day", "asset"]).agg(pl.col("funding_rate").sum().alias("funding"))

    df = (
        k.join(oc.drop("date"), on=["day", "asset"], how="left")
        .join(fday, on=["day", "asset"], how="left")
        .with_columns(pl.col("funding").fill_null(0.0))
        .sort(["asset", "day"])
    )

    # signals (per asset, trailing only), then LAG-day publication shift
    df = df.with_columns(
        pl.col("adr_act").rolling_mean(window_size=7, min_samples=7).over("asset").alias("adr7"),
        pl.col("adr_act").rolling_mean(window_size=90, min_samples=90).over("asset").alias("adr90"),
        pl.col("adr_act").rolling_mean(window_size=30, min_samples=30).over("asset").alias("adr30"),
        pl.col("adr_act").rolling_mean(window_size=180, min_samples=180).over("asset").alias("adr180"),
        pl.col("tx_tfr").rolling_mean(window_size=14, min_samples=14).over("asset").alias("tfr14"),
    ).with_columns(
        ((pl.col("adr7") + 1).log() - (pl.col("adr90") + 1).log()).alias("adrg_s"),
        ((pl.col("adr30") + 1).log() - (pl.col("adr180") + 1).log()).alias("adrg_l"),
        (pl.col("mcap_usd") / (pl.col("tfr14") + 1)).log().alias("nvt"),
        pl.col("mvrv").alias("mvrv_s"),
        (pl.col("close") / pl.col("close").shift(30).over("asset") - 1.0).alias("mom30"),
    ).with_columns(
        pl.col("adrg_s", "adrg_l", "nvt", "mvrv_s").shift(LAG).over("asset")
    )
    return df


def build(df: pl.DataFrame, sig_col: str, sign: float):
    """Wide arrays. sign=+1 → long high signal; sign=−1 → long low signal."""
    days = df["day"].unique().sort()
    assets = df["asset"].unique().sort()
    di = {d: i for i, d in enumerate(days)}
    ai = {a: i for i, a in enumerate(assets)}
    T, N = len(days), len(assets)
    ret = np.full((T, N), np.nan)
    fund = np.full((T, N), np.nan)
    sig = np.full((T, N), np.nan)
    liq = np.full((T, N), np.nan)
    for d, a, r, fu, s, l in df.select(["day", "asset", "ret", "funding", sig_col, "liq_med"]).rows():
        i, j = di[d], ai[a]
        if r is not None:
            ret[i, j] = r
        fund[i, j] = fu
        if s is not None:
            sig[i, j] = sign * s
        if l is not None:
            liq[i, j] = l
    elig = ~np.isnan(sig) & ~np.isnan(ret) & (np.nan_to_num(liq, nan=0.0) >= LIQ_FLOOR)
    return ret, fund, sig, elig, days.to_numpy()


def run_strategy(ret, fund, sig, elig, q: float, rng=None):
    """Long top-quantile of (signed) signal / short bottom; held over t+1."""
    T, N = ret.shape
    pnl = np.zeros(T)
    w_prev = np.zeros(N)
    for t in range(T - 1):
        idx = np.where(elig[t])[0]
        w = np.zeros(N)
        if idx.size >= MIN_XS:
            s = sig[t, idx]
            if rng is not None:
                s = rng.permutation(s)
            k = max(1, int(round(q * idx.size)))
            order = np.argsort(s)
            w[idx[order[-k:]]] = +0.5 / k
            w[idx[order[:k]]] = -0.5 / k
        r1 = np.nan_to_num(ret[t + 1], nan=0.0)
        f1 = np.nan_to_num(fund[t + 1], nan=0.0)
        pnl[t + 1] = float(w @ r1 - w @ f1 - np.abs(w - w_prev).sum() * FEE)
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


def momentum_ic(df: pl.DataFrame, sig_col: str) -> float:
    """Mean daily cross-sectional Spearman IC of signal vs trailing 30d return."""
    sub = df.drop_nulls([sig_col, "mom30"]).filter(pl.col("liq_med") >= LIQ_FLOOR)
    ics = (
        sub.group_by("day")
        .agg(pl.corr(pl.col(sig_col).rank(), pl.col("mom30").rank()).alias("ic"),
             pl.len().alias("n"))
        .filter(pl.col("n") >= MIN_XS)
    )
    return float(ics["ic"].drop_nulls().mean() or 0.0)


def main() -> None:
    df = load()
    n_oc = df.filter(pl.col("mvrv_s").is_not_null())["asset"].n_unique()
    print(f"panel rows={df.height}  assets with on-chain={n_oc}  "
          f"days {df['day'].min()}..{df['day'].max()}")

    # configs: (label, column, sign per FIXED hypothesis)
    SIGS = [
        ("mvrv (long low)", "mvrv_s", -1.0),
        ("adr-growth 7/90 (long high)", "adrg_s", +1.0),
        ("adr-growth 30/180 (long high)", "adrg_l", +1.0),
        ("nvt (long low)", "nvt", -1.0),
    ]
    GRID_Q = [0.2, 0.3]

    all_days = df["day"].unique().sort()
    split = all_days[int(len(all_days) * IS_FRAC)]
    print(f"IS/OOS split day={split}\n")
    print("signal-vs-mom30 cross-sectional IC (is it momentum in disguise?):")
    for lbl, col, _ in SIGS:
        print(f"  {lbl:<32} IC={momentum_ic(df, col):+.3f}")

    results = []
    pnl_cols = []
    print()
    for lbl, col, sign in SIGS:
        ret, fund, sig, elig, days = build(df, col, sign)
        is_mask = days < split
        for q in GRID_Q:
            pnl = run_strategy(ret, fund, sig, elig, q)
            sr_is = ann_sharpe(pnl[is_mask])
            results.append({"lbl": lbl, "col": col, "sign": sign, "q": q,
                            "sr_is": sr_is, "pnl": pnl, "days": days})
            pnl_cols.append(pnl)
            print(f"  {lbl:<32} q={q:.1f}  IS SR={sr_is:+.2f}  "
                  f"IS ann={pnl[is_mask].mean()*PPY:+.1%}")

    L = min(len(p) for p in pnl_cols)
    M = np.stack([p[:L] for p in pnl_cols], axis=1)
    print(f"\nCSCV PBO over {len(pnl_cols)} configs: {cscv_pbo(M, n_splits=16):.2f}")

    best = max(results, key=lambda r: r["sr_is"])
    days = best["days"]
    oos = best["pnl"][days >= split]
    trials = [r["sr_is"] / math.sqrt(PPY) for r in results]
    dsr = float(deflated_sharpe_ratio(oos, trials)) if oos.size >= 2 else None
    print(f"\nIS-best: {best['lbl']} q={best['q']} (IS SR {best['sr_is']:+.2f})")
    print(f"OOS: SR={ann_sharpe(oos):+.2f}  ann={oos.mean()*PPY:+.1%}  "
          f"maxDD={max_dd(oos):+.1%}  days={oos.size}")
    print(f"OOS DSR (deflated by {len(results)} trials)={dsr}")

    # placebo on IS-best, OOS window
    ret, fund, sig, elig, days = build(df, best["col"], best["sign"])
    om = days >= split
    rng = np.random.default_rng(SEED)
    real = ann_sharpe(best["pnl"][om])
    ps = np.array([ann_sharpe(run_strategy(ret, fund, sig, elig, best["q"], rng=rng)[om])
                   for _ in range(N_PLACEBO)])
    beats = int((real > ps).sum())
    print(f"placebo ×{N_PLACEBO}: real OOS SR {real:+.2f} beats {beats}/{N_PLACEBO}  "
          f"placebo mean={ps.mean():+.2f} p95={np.quantile(ps, 0.95):+.2f}")


if __name__ == "__main__":
    main()
