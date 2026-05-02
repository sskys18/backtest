"""Relative strength versus BTC."""

from __future__ import annotations

import polars as pl


def compute_rs(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """Append `rs_btc`; values above 1.0 outperform BTC over the window."""
    window = int(params.get("window", 60))
    btc_col = params.get("btc_col", "btc_close")
    if btc_col not in df.columns:
        return df.with_columns(pl.lit(1.0).alias("rs_btc"))

    return df.with_columns(
        (
            (pl.col("close") / pl.col("close").shift(window))
            / (pl.col(btc_col) / pl.col(btc_col).shift(window))
        ).alias("rs_btc")
    )
