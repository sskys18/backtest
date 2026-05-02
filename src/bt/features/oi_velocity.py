"""Open-interest velocity, acceleration, and z-score features."""

from __future__ import annotations

import polars as pl


def compute_oi_velocity(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """Append OI first derivative, second derivative, and rolling z-score."""
    window = int(params.get("window", 60))
    df = df.with_columns(
        pl.col("open_interest").diff().alias("oi_velocity")
    )
    return df.with_columns([
        pl.col("oi_velocity").diff().alias("oi_accel"),
        (
            (pl.col("oi_velocity") - pl.col("oi_velocity").rolling_mean(window))
            / pl.col("oi_velocity").rolling_std(window)
        ).alias("oi_z"),
    ])
