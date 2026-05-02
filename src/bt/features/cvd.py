"""Cumulative Volume Delta feature.

CVD[t] = sum_{i<=t}(buy_volume[i] - sell_volume[i]).
Requires `buy_volume` and `sell_volume` columns from trades data.
"""

from __future__ import annotations

import polars as pl


def compute_cvd(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """Append cumulative volume delta."""
    return df.with_columns(
        (pl.col("buy_volume") - pl.col("sell_volume")).cum_sum().alias("cvd")
    )
