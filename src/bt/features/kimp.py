"""Kimchi premium feature."""

from __future__ import annotations

import polars as pl


def compute_kimp(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """Append kimchi premium percentage.

    kimp = ((krw_price / fx_rate) / usd_price - 1) * 100
    """
    upbit_col = params.get("upbit_col", "upbit_close")
    binance_col = params.get("binance_col", "binance_close")
    fx_col = params.get("fx_col", "fx_rate")
    return df.with_columns(
        ((pl.col(upbit_col) / pl.col(fx_col) / pl.col(binance_col) - 1.0) * 100.0).alias("kimp")
    )
