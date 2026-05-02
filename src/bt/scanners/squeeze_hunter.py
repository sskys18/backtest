"""squeeze-hunter scanner port.

APPROXIMATION: original validation-layer weights are private. This port uses
four tunable layers inferred from public UI text: OI velocity, CVD agreement,
liquidation spike, and top-trader LSR confirmation.
"""

from __future__ import annotations

import polars as pl

from bt.replay.simulator import ExitRules
from bt.scanners.registry import register


class SqueezeHunterSpec:
    name = "squeeze_hunter"
    required_columns = ["oi_velocity", "cvd", "liq_z", "toptrader_lsr"]
    is_approximation = True

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=2.0, sl_pct=1.0, max_hold_bars=45)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        accel_min = float(params.get("oi_velocity_min", 0.0))
        cvd_min = float(params.get("cvd_min", 0.0))
        liq_z_min = float(params.get("liq_z_min", 1.5))
        lsr_threshold = float(params.get("lsr_threshold", 1.5))
        # APPROXIMATION: all four validation layers are required by default.
        layers = (
            (pl.col("oi_velocity") > accel_min).cast(pl.Int64)
            + (pl.col("cvd").diff() > cvd_min).fill_null(False).cast(pl.Int64)
            + (pl.col("liq_z") > liq_z_min).cast(pl.Int64)
            + (pl.col("toptrader_lsr") > lsr_threshold).cast(pl.Int64)
        )
        return df.with_columns(
            layers.alias("squeeze_layers")
        ).with_columns([
            (pl.col("squeeze_layers") >= 4).cast(pl.Int64).alias("signal"),
            pl.col("squeeze_layers").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params: dict):
        return None


register(SqueezeHunterSpec())
