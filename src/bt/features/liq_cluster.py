"""Liquidation cluster features.

Degrades to zeros when liquidation columns are missing.
"""

from __future__ import annotations

import polars as pl


def compute_liq_cluster(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """Append liquidation intensity, imbalance, and rolling z-score."""
    window = int(params.get("window", 60))
    has_liq = "long_liq_usd" in df.columns and "short_liq_usd" in df.columns
    if not has_liq:
        return df.with_columns([
            pl.lit(0.0).alias("liq_intensity"),
            pl.lit(0.0).alias("liq_imbalance"),
            pl.lit(0.0).alias("liq_z"),
        ])

    df = df.with_columns(
        (pl.col("long_liq_usd") + pl.col("short_liq_usd")).alias("liq_intensity")
    )
    return df.with_columns([
        pl.when(pl.col("liq_intensity") > 0)
        .then((pl.col("long_liq_usd") - pl.col("short_liq_usd")) / pl.col("liq_intensity"))
        .otherwise(0.0)
        .alias("liq_imbalance"),
        (
            (pl.col("liq_intensity") - pl.col("liq_intensity").rolling_mean(window))
            / pl.col("liq_intensity").rolling_std(window)
        ).fill_nan(0.0).fill_null(0.0).alias("liq_z"),
    ])
