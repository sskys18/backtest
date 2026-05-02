"""liqpressuremap scanner port.

Source: https://akakin83.github.io/liqpressuremap/
Public spec basis: 3-sigma liquidation velocity ignition and strong direction
bias at abs(bias) >= 0.6.
"""

from __future__ import annotations

import polars as pl

from bt.replay.simulator import ExitRules
from bt.scanners.registry import register


class LiqPressureMapSpec:
    name = "liqpressuremap"
    required_columns = ["liq_intensity", "liq_z", "liq_imbalance"]
    is_approximation = False

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=1.5, sl_pct=0.8, max_hold_bars=30)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        z_threshold = float(params.get("z_threshold", 3.0))
        bias_threshold = float(params.get("bias_threshold", 0.6))
        signal = (
            (pl.col("liq_z") >= z_threshold)
            & (pl.col("liq_imbalance").abs() >= bias_threshold)
        ).cast(pl.Int64)
        return df.with_columns([
            signal.alias("signal"),
            pl.col("liq_z").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params: dict):
        return None


register(LiqPressureMapSpec())
