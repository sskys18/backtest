"""Build BTC-USDT scanner-tuning feature parquet.

Joins canonical bt data (binance OHLCV, binance_vision metrics, funding,
optional CoinAnk liquidations) into a single 5m-aligned parquet ready for
`bt scanner-tune`.

Usage:
  python scripts/build_btc_scanner_features.py [--with-liq]

Without --with-liq: liquidation columns are zero-filled (degraded).
With --with-liq: fetches real CoinAnk liquidations at 4h granularity and
forward-fills to 5m bars. Requires CoinAnk to be reachable (no 403).

Output: data/features/btc_usdt_scanner_tuning.parquet
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

OHLCV_PATH = "data/raw/binance/BTC-USDT/ohlcv/5m/data.parquet"
METRICS_PATH = "data/raw/binance_vision/BTC-USDT/metrics/1m/data.parquet"
FUNDING_PATH = "data/raw/binance/BTC-USDT/funding/data.parquet"
OUT_PATH = Path("data/features/btc_usdt_scanner_tuning.parquet")
BUCKET_5M = 5 * 60 * 1000


async def _fetch_coinank_liq(start: datetime, end: datetime) -> pl.DataFrame:
    from bt.data.sources.coinank import CoinankSource
    return await CoinankSource().fetch(
        pair="BTC-USDT", data_type="liquidations", timeframe="4h",
        start=start, end=end,
    )


def build(with_liq: bool) -> pl.DataFrame:
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

    if with_liq:
        ts_min = ohlcv["timestamp"].min()
        ts_max = ohlcv["timestamp"].max()
        start_dt = datetime.fromtimestamp(ts_min / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(ts_max / 1000, tz=timezone.utc)
        liq = asyncio.run(_fetch_coinank_liq(start_dt, end_dt))
        if len(liq) == 0:
            print("WARN: CoinAnk returned 0 rows. Falling back to zero-stub.")
            df = df.with_columns([
                pl.lit(0.0).alias("liq_intensity"),
                pl.lit(0.0).alias("liq_z"),
                pl.lit(0.0).alias("liq_imbalance"),
            ])
        else:
            liq = liq.sort("timestamp").with_columns(
                (pl.col("long_liq_usd") + pl.col("short_liq_usd")).alias("liq_intensity"),
                pl.when((pl.col("long_liq_usd") + pl.col("short_liq_usd")) > 0)
                    .then((pl.col("long_liq_usd") - pl.col("short_liq_usd"))
                          / (pl.col("long_liq_usd") + pl.col("short_liq_usd")))
                    .otherwise(0.0).alias("liq_imbalance"),
            )
            liq = liq.with_columns(
                ((pl.col("liq_intensity") - pl.col("liq_intensity").rolling_mean(60))
                 / pl.col("liq_intensity").rolling_std(60))
                    .fill_nan(0.0).fill_null(0.0).alias("liq_z"),
            )
            df = df.sort("timestamp").join_asof(
                liq.select(["timestamp", "liq_intensity", "liq_imbalance", "liq_z"]),
                on="timestamp", strategy="backward",
            )
            df = df.with_columns([
                pl.col("liq_intensity").fill_null(0.0),
                pl.col("liq_imbalance").fill_null(0.0),
                pl.col("liq_z").fill_null(0.0),
            ])
    else:
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
    p = argparse.ArgumentParser()
    p.add_argument("--with-liq", action="store_true",
                   help="Fetch real CoinAnk liquidations (requires non-rate-limited access)")
    args = p.parse_args()

    df = build(args.with_liq)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(OUT_PATH)
    nz = int((df["liq_intensity"] > 0).sum())
    print(f"saved: {OUT_PATH}")
    print(f"  rows={len(df)}")
    print(f"  cols={df.columns}")
    print(f"  liq_intensity nonzero rows={nz}/{len(df)} ({nz/len(df)*100:.1f}%)")


if __name__ == "__main__":
    main()
