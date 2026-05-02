"""Stage gate state machine.

APPROXIMATION: original alpha-hunter formulas are not public. Defaults are
inferred from public UI text around OI build, CVD agreement, and funding
extremes. Thresholds are tunable via params.
"""

from __future__ import annotations

import polars as pl


def compute_stage_gate(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """Append ordinal stage from OI, CVD, and funding gates."""
    oi_z_min = float(params.get("oi_z_threshold", 1.5))  # APPROXIMATION
    cvd_slope_min = float(params.get("cvd_slope_min", 50.0))  # APPROXIMATION
    funding_extreme = float(params.get("funding_extreme", 0.001))  # APPROXIMATION
    window = int(params.get("window_bars", 12))

    df = df.with_columns(
        (pl.col("cvd") - pl.col("cvd").shift(window)).alias("_cvd_slope")
    )
    df = df.with_columns([
        (pl.col("oi_z").fill_null(0.0) >= oi_z_min).cast(pl.Int64).alias("_g1_oi"),
        (pl.col("_cvd_slope").fill_null(0.0) >= cvd_slope_min).cast(pl.Int64).alias("_g2_cvd"),
        (pl.col("funding_rate").fill_null(0.0).abs() >= funding_extreme).cast(pl.Int64).alias("_g3_funding"),
    ])
    return df.with_columns(
        (pl.col("_g1_oi") + pl.col("_g2_cvd") + pl.col("_g3_funding")).alias("stage")
    ).drop(["_cvd_slope", "_g1_oi", "_g2_cvd", "_g3_funding"])
