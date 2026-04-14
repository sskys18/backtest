"""TimesFM forecast-based strategy."""

from __future__ import annotations

import polars as pl


class TimesFMStrategy:
    """Uses TimesFM forecast columns to generate trading signals.

    Goes long when forecast_return > entry_threshold (model predicts price increase).
    Exits when forecast_return < exit_threshold.
    """

    def __init__(
        self,
        entry_threshold: float = 0.005,
        exit_threshold: float = 0.0,
        position_size: float = 0.15,
        forecast_col: str = "forecast_return",
    ):
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.position_size = position_size
        self.forecast_col = forecast_col

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        if self.forecast_col not in df.columns:
            raise ValueError(
                f"Column '{self.forecast_col}' not found. "
                "Run bt features build with forecast indicator first."
            )

        forecast = df[self.forecast_col].fill_null(0.0).fill_nan(0.0)

        # Signal: 1.0 when forecast return > entry threshold
        strength = (forecast > self.entry_threshold).cast(pl.Float64).fill_null(0.0)

        return pl.DataFrame({
            "strength": strength,
            "target_size": pl.Series("target_size", [self.position_size] * len(df)),
        })
