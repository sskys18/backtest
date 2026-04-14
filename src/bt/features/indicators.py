"""Technical indicators implemented with Polars expressions."""

from __future__ import annotations

import polars as pl


def sma(df: pl.DataFrame, input_col: str, period: int, output_col: str) -> pl.DataFrame:
    """Simple Moving Average."""
    return df.with_columns(
        pl.col(input_col).rolling_mean(period).alias(output_col)
    )


def ema(df: pl.DataFrame, input_col: str, period: int, output_col: str) -> pl.DataFrame:
    """Exponential Moving Average."""
    return df.with_columns(
        pl.col(input_col).ewm_mean(span=period).alias(output_col)
    )


def rsi(df: pl.DataFrame, input_col: str, period: int, output_col: str) -> pl.DataFrame:
    """Relative Strength Index."""
    delta = pl.col(input_col).diff()
    gain = delta.clip(lower_bound=0.0).rolling_mean(period)
    loss = (-delta.clip(upper_bound=0.0)).rolling_mean(period)

    # RS = avg_gain / avg_loss, RSI = 100 - 100 / (1 + RS)
    rs = gain / loss
    rsi_expr = (100.0 - 100.0 / (1.0 + rs))

    return df.with_columns(rsi_expr.alias(output_col))


def bollinger(
    df: pl.DataFrame, input_col: str, period: int, num_std: float,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """Bollinger Bands — returns upper, middle, lower bands."""
    cols = columns or ["bb_upper", "bb_middle", "bb_lower"]
    middle = pl.col(input_col).rolling_mean(period)
    std = pl.col(input_col).rolling_std(period)

    return df.with_columns([
        (middle + num_std * std).alias(cols[0]),
        middle.alias(cols[1]),
        (middle - num_std * std).alias(cols[2]),
    ])


def vwap(df: pl.DataFrame, period: int, output_col: str) -> pl.DataFrame:
    """Volume Weighted Average Price (rolling)."""
    tp = (pl.col("high") + pl.col("low") + pl.col("close")) / 3.0
    tp_vol = tp * pl.col("volume")

    return df.with_columns(
        (tp_vol.rolling_sum(period) / pl.col("volume").rolling_sum(period)).alias(output_col)
    )


def premium(
    df: pl.DataFrame,
    input_cols: list[str],
    output_col: str,
) -> pl.DataFrame:
    """Kimchi premium: (local_price / (global_price * fx_rate) - 1) * 100.

    input_cols: [global_close, local_close, fx_rate]
    """
    if len(input_cols) != 3:
        raise ValueError("premium requires exactly 3 input columns: [global_close, local_close, fx_rate]")

    global_close, local_close, fx_rate = input_cols
    prem = (pl.col(local_close) / (pl.col(global_close) * pl.col(fx_rate)) - 1.0) * 100.0

    return df.with_columns(prem.alias(output_col))


def funding_ma(
    df: pl.DataFrame, input_col: str, period: int, output_col: str,
) -> pl.DataFrame:
    """Moving average of funding rate."""
    return sma(df, input_col, period, output_col)


def forecast(
    df: pl.DataFrame, input_col: str, context_length: int, horizon: int,
    output_col: str | None = None,
) -> pl.DataFrame:
    """TimesFM forecast indicator — delegates to timesfm_forecast module."""
    from bt.features.timesfm_forecast import generate_forecasts
    return generate_forecasts(df, input_col, context_length, horizon)
