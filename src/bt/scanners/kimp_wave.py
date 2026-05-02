"""kimp-wave scanner port.

Source: https://akakin83.github.io/kimp-wave/
Public spec basis: kimp >= 1.5%, 24h volume >= $50M, pool volatility >= 20%.
Pool-volatility filtering is omitted because pool data is not in the feature
DataFrame.
"""

from __future__ import annotations

import polars as pl

from bt.replay.simulator import ExitRules
from bt.scanners.base import EntryFilter
from bt.scanners.registry import register


class KimpWaveSpec:
    name = "kimp_wave"
    required_columns = ["kimp", "volume"]
    is_approximation = False

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=2.0, sl_pct=1.0, max_hold_bars=120)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        kimp_threshold = float(params.get("kimp_threshold", 1.5))
        vol_min = float(params.get("vol_min", 50_000_000.0))
        signal = (
            (pl.col("kimp") >= kimp_threshold)
            & (pl.col("volume") >= vol_min)
        ).cast(pl.Int64)
        return df.with_columns([
            signal.alias("signal"),
            pl.col("kimp").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params: dict) -> EntryFilter | None:
        return None


register(KimpWaveSpec())
