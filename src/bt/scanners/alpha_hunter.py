"""alpha-hunter scanner port.

APPROXIMATION: original Stage Gate formulas, weights, and private conditions
are not public. This port uses the derived `stage` feature from
`features/stage_gate.py`, which combines OI z-score, CVD slope, and funding
extremes into an ordinal stage. Entry defaults to S2.
"""

from __future__ import annotations

import polars as pl

from bt.replay.simulator import ExitRules
from bt.scanners.registry import register


class AlphaHunterSpec:
    name = "alpha_hunter"
    required_columns = ["oi_z", "cvd", "funding_rate", "stage"]
    is_approximation = True

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=2.5, sl_pct=1.2, max_hold_bars=60)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        entry_stage = int(params.get("entry_stage", 2))  # APPROXIMATION
        signal = (pl.col("stage") >= entry_stage).cast(pl.Int64)
        return df.with_columns([
            signal.alias("signal"),
            pl.col("stage").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params: dict):
        return None


register(AlphaHunterSpec())
