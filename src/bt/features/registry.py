"""Indicator registry — maps names to functions."""

from __future__ import annotations

from typing import Any, Callable

import polars as pl

from bt.features import indicators
from bt.features.cvd import compute_cvd
from bt.features.kimp import compute_kimp
from bt.features.liq_cluster import compute_liq_cluster
from bt.features.oi_velocity import compute_oi_velocity
from bt.features.rs import compute_rs
from bt.features.stage_gate import compute_stage_gate

# Registry: name → function that takes (df, **config) → df
IndicatorFn = Callable[..., pl.DataFrame]

INDICATOR_REGISTRY: dict[str, IndicatorFn] = {
    "sma": indicators.sma,
    "ema": indicators.ema,
    "rsi": indicators.rsi,
    "bollinger": indicators.bollinger,
    "vwap": indicators.vwap,
    "premium": indicators.premium,
    "funding_ma": indicators.funding_ma,
    "forecast": indicators.forecast,
    "cvd": compute_cvd,
    "kimp": compute_kimp,
    "oi_velocity": compute_oi_velocity,
    "liq_cluster": compute_liq_cluster,
    "stage_gate": compute_stage_gate,
    "rs": compute_rs,
}

_DERIVED_INDICATORS = {
    "cvd",
    "kimp",
    "oi_velocity",
    "liq_cluster",
    "stage_gate",
    "rs",
}


def apply_indicator(
    df: pl.DataFrame,
    name: str,
    input_col: str | list[str],
    params: dict[str, Any],
    output_col: str | None = None,
    output_cols: list[str] | None = None,
) -> pl.DataFrame:
    """Apply a registered indicator to a DataFrame."""
    fn = INDICATOR_REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"Unknown indicator: {name}. Known: {list(INDICATOR_REGISTRY.keys())}")

    if name in _DERIVED_INDICATORS:
        return fn(df, params)
    elif name == "bollinger":
        return fn(df, input_col=input_col, columns=output_cols, **params)
    elif name == "premium":
        return fn(df, input_cols=input_col, output_col=output_col or "premium", **params)
    elif name == "vwap":
        return fn(df, output_col=output_col or "vwap", **params)
    elif name == "forecast":
        return fn(df, input_col=input_col, output_col=output_col, **params)
    else:
        return fn(df, input_col=input_col, output_col=output_col or f"{name}_{params.get('period', '')}", **params)
