"""alpha-terminal scanner port.

Source: https://akakin83.github.io/alpha-terminal/

APPROXIMATION: original 18-factor Alpha Score weights are private. This port
uses five tunable factors inferred from public UI text:
close above VWAP, relative strength, liquidation z-score, OI z-score, and
optional funding extreme.
"""

from __future__ import annotations

import polars as pl

from bt.replay.simulator import ExitRules
from bt.scanners.registry import register


class AlphaTerminalSpec:
    name = "alpha_terminal"
    required_columns = ["close", "vwap", "rs_btc", "liq_z", "oi_z"]
    is_approximation = True

    def default_exit_rules(self) -> ExitRules:
        return ExitRules(tp_pct=3.0, sl_pct=1.5, max_hold_bars=120)

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        # APPROXIMATION: weights are inferred defaults and are tunable.
        w_vwap = int(params.get("w_vwap", 25))
        w_rs = int(params.get("w_rs", 25))
        w_liq = int(params.get("w_liq", 20))
        w_oi = int(params.get("w_oi", 15))
        w_fund = int(params.get("w_fund", 15))
        threshold = int(params.get("score_threshold", 55))

        score = (
            pl.when(pl.col("close") > pl.col("vwap")).then(w_vwap).otherwise(0)
            + pl.when(pl.col("rs_btc") > 1.2).then(w_rs).otherwise(0)
            + pl.when(pl.col("liq_z") > 2.0).then(w_liq).otherwise(0)
            + pl.when(pl.col("oi_z") > 1.5).then(w_oi).otherwise(0)
        )
        if "funding_rate" in df.columns:
            score = score + pl.when(pl.col("funding_rate").abs() > 0.001).then(w_fund).otherwise(0)

        return df.with_columns(
            score.alias("alpha_score")
        ).with_columns([
            (pl.col("alpha_score") >= threshold).cast(pl.Int64).alias("signal"),
            pl.col("alpha_score").cast(pl.Float64).alias("strength"),
        ])

    def entry_filter(self, params: dict):
        return None


register(AlphaTerminalSpec())
