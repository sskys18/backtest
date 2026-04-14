"""Mean reversion strategy."""

from __future__ import annotations

import polars as pl


class MeanRevertStrategy:
    """Bollinger Band / RSI mean reversion strategy.

    Goes long when price drops below lower Bollinger Band (oversold).
    Exits when price returns to middle band.
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        position_size: float = 0.15,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.position_size = position_size

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        close = df["close"]
        middle = close.rolling_mean(self.bb_period)
        std = close.rolling_std(self.bb_period)
        lower = middle - self.bb_std * std

        # Entry: close < lower band (oversold)
        # Exit: close >= middle band (mean reversion complete)
        # Use a simple state: 1.0 when below lower, 0.0 when above middle
        below_lower = (close < lower).cast(pl.Float64).fill_null(0.0)
        above_middle = (close >= middle).cast(pl.Float64).fill_null(0.0)

        # Build signal with state tracking:
        # Once below_lower fires (1.0), stay long until above_middle fires
        strength_list = []
        in_signal = False
        below_vals = below_lower.to_list()
        above_vals = above_middle.to_list()

        for i in range(len(df)):
            if not in_signal and below_vals[i] == 1.0:
                in_signal = True
            elif in_signal and above_vals[i] == 1.0:
                in_signal = False
            strength_list.append(1.0 if in_signal else 0.0)

        return pl.DataFrame({
            "strength": pl.Series("strength", strength_list),
            "target_size": pl.Series("target_size", [self.position_size] * len(df)),
        })
