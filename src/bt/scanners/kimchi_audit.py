"""kimchi-audit-picks scanner port.

Source: https://londonpotato1.github.io/kimchi-audit-picks/
Universe-selection scanner over per-pair snapshot metadata.
"""

from __future__ import annotations

import polars as pl

from bt.replay.simulator import ExitRules
from bt.scanners.registry import register


class KimchiAuditSpec:
    name = "kimchi_audit"
    required_columns = ["pair", "score", "bithumb_concentration", "whale_pct"]
    is_approximation = False

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=5.0, sl_pct=2.0, max_hold_bars=1440)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        score_min = float(params.get("score_min", 55.0))
        concentration_min = float(params.get("concentration_min", 0.5))
        whale_pct_min = float(params.get("whale_pct_min", 0.0))
        signal = (
            (pl.col("score") >= score_min)
            & (pl.col("bithumb_concentration") >= concentration_min)
            & (pl.col("whale_pct") >= whale_pct_min)
        ).cast(pl.Int64)
        return df.with_columns([
            signal.alias("signal"),
            pl.col("score").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params: dict):
        return None


register(KimchiAuditSpec())
