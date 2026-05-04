"""nahonja-scanner port.

Source: https://akakin83.github.io/nahonja-scanner/

APPROXIMATION: original 8-signal weights are private. This port maps public UI
labels to nearest available factors: momentum, taker volume, OI build,
compression, funding extreme, OI/CVD divergence, liquidation bounce, and early
acceleration.
"""

from __future__ import annotations

import polars as pl

from bt.replay.simulator import ExitRules
from bt.scanners.registry import register


class NahonjaSpec:
    name = "nahonja"
    required_columns = ["oi_z", "taker_ratio", "funding_rate", "liq_z", "cvd", "rs_btc"]
    is_approximation = True

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=2.5, sl_pct=1.2, max_hold_bars=60)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        threshold = int(params.get("score_threshold", 50))
        # APPROXIMATION: each inferred factor contributes roughly one eighth.
        score = (
            pl.when(pl.col("rs_btc") > 1.1).then(12).otherwise(0)
            + pl.when(pl.col("taker_ratio") > 1.2).then(12).otherwise(0)
            + pl.when(pl.col("oi_z") > 1.5).then(12).otherwise(0)
            + pl.when(pl.col("oi_z").abs() < 0.3).then(12).otherwise(0)
            + pl.when(pl.col("funding_rate").abs() > 0.001).then(12).otherwise(0)
            + pl.when((pl.col("oi_z") > 0) & (pl.col("cvd").diff() < 0)).then(12).otherwise(0)
            + pl.when(pl.col("liq_z") > 2.0).then(12).otherwise(0)
            + pl.when(pl.col("oi_z").diff() > 0.5).then(16).otherwise(0)
        )
        return df.with_columns(
            score.alias("nahonja_score")
        ).with_columns([
            (pl.col("nahonja_score") >= threshold).cast(pl.Int64).alias("signal"),
            pl.col("nahonja_score").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params: dict):
        return None


register(NahonjaSpec())
