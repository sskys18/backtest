"""Momentum / trend-following strategy."""

from __future__ import annotations

import polars as pl


class MomentumStrategy:
    """SMA crossover momentum strategy.

    Goes long when fast MA > slow MA.
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        position_size: float = 0.15,
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_size = position_size

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        fast_ma = df["close"].rolling_mean(self.fast_period)
        slow_ma = df["close"].rolling_mean(self.slow_period)

        # Signal: 1.0 when fast > slow, 0.0 otherwise
        strength = (fast_ma > slow_ma).cast(pl.Float64).fill_null(0.0)

        return pl.DataFrame({
            "strength": strength,
            "target_size": pl.Series("target_size", [self.position_size] * len(df)),
        })
