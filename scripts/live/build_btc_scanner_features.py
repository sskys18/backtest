"""Build BTC-USDT scanner-tuning feature parquet.

Joins already-imported bt data (binance OHLCV, binance_vision metrics, funding)
into a single 5m-aligned parquet ready for `bt scanner-tune`. Reads parquet only —
this repo no longer fetches; import data via `bt data import` first.

Usage:
  python scripts/build_btc_scanner_features.py

Liquidation columns (liq_intensity/liq_z/liq_imbalance) are zero-filled. To use
real liquidations, import a CoinAnk parquet and join it in build().

Output: data/features/btc_usdt_scanner_tuning.parquet
"""
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

OHLCV_PATH = "data/raw/binance/BTC-USDT/ohlcv/5m/data.parquet"
METRICS_PATH = "data/raw/binance_vision/BTC-USDT/metrics/1m/data.parquet"
FUNDING_PATH = "data/raw/binance/BTC-USDT/funding/data.parquet"
OUT_PATH = Path("data/features/btc_usdt_scanner_tuning.parquet")
BUCKET_5M = 5 * 60 * 1000


def build() -> pl.DataFrame:
    ohlcv = pl.read_parquet(OHLCV_PATH)
    metrics = pl.read_parquet(METRICS_PATH)
    funding = pl.read_parquet(FUNDING_PATH)

    metrics = metrics.with_columns(
        (pl.col("timestamp") // BUCKET_5M * BUCKET_5M).alias("timestamp")
    ).group_by("timestamp").agg([
        pl.col("open_interest").last(),
        pl.col("toptrader_lsr").last(),
        pl.col("global_lsr").last(),
        pl.col("taker_ratio").last(),
    ])

    df = ohlcv.join(metrics, on="timestamp", how="left").sort("timestamp")
    funding_sorted = funding.sort("timestamp").select(["timestamp", "funding_rate"])
    df = df.join_asof(funding_sorted, on="timestamp", strategy="backward")
    df = df.with_columns(pl.col("funding_rate").fill_null(0.0001))

    df = df.with_columns([
        ((2 * pl.col("taker_ratio").fill_null(1.0) - 1) * pl.col("volume"))
            .cum_sum().alias("cvd"),
        pl.col("open_interest").diff().alias("oi_velocity"),
        ((pl.col("close") * pl.col("volume")).rolling_sum(20)
         / pl.col("volume").rolling_sum(20)).alias("vwap"),
    ])
    df = df.with_columns([
        pl.col("oi_velocity").diff().alias("oi_accel"),
        ((pl.col("oi_velocity") - pl.col("oi_velocity").rolling_mean(60))
         / pl.col("oi_velocity").rolling_std(60)).alias("oi_z"),
    ])

    df = df.with_columns(pl.lit(1.05).alias("rs_btc"))

    # Liquidation columns: zero-filled. Live fetching was removed (this repo is
    # import-only). To use real liquidations, import a CoinAnk parquet via
    # `bt data import` and join it here.
    df = df.with_columns([
        pl.lit(0.0).alias("liq_intensity"),
        pl.lit(0.0).alias("liq_z"),
        pl.lit(0.0).alias("liq_imbalance"),
    ])

    df = df.with_columns(
        (pl.col("cvd") - pl.col("cvd").shift(12)).alias("_cvd_slope")
    )
    df = df.with_columns(
        ((pl.col("oi_z").fill_null(0) >= 1.5).cast(pl.Int64)
         + (pl.col("_cvd_slope").fill_null(0) >= 50).cast(pl.Int64)
         + (pl.col("funding_rate").fill_null(0).abs() >= 0.001).cast(pl.Int64))
            .alias("stage")
    ).drop("_cvd_slope")

    df = df.drop_nulls(subset=["oi_z", "vwap", "cvd"])
    return df


def main():
    argparse.ArgumentParser().parse_args()  # no flags; --help only

    df = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    nz = int((df["liq_intensity"] > 0).sum())
    print(f"saved: {OUT_PATH}")
    print(f"  rows={len(df)}")
    print(f"  cols={df.columns}")
    print(f"  liq_intensity nonzero rows={nz}/{len(df)} ({nz/len(df)*100:.1f}%)")


if __name__ == "__main__":
    main()
