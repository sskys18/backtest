"""Canonical schemas for each data type."""

from __future__ import annotations

import polars as pl

# Column name → Polars dtype for each data type
SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "ohlcv": {
        "timestamp": pl.Int64,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    },
    "funding": {
        "timestamp": pl.Int64,
        "funding_rate": pl.Float64,
        "mark_price": pl.Float64,
    },
    "oi": {
        "timestamp": pl.Int64,
        "open_interest": pl.Float64,
    },
    "depth": {
        "timestamp": pl.Int64,
        "bid_price_1": pl.Float64,
        "bid_qty_1": pl.Float64,
        "ask_price_1": pl.Float64,
        "ask_qty_1": pl.Float64,
        "spread": pl.Float64,
        "mid_price": pl.Float64,
    },
    "trades": {
        "timestamp": pl.Int64,
        "num_trades": pl.UInt64,
        "buy_volume": pl.Float64,
        "sell_volume": pl.Float64,
        "vwap": pl.Float64,
    },
    "liquidations": {
        "timestamp": pl.Int64,
        "long_liq_usd": pl.Float64,
        "short_liq_usd": pl.Float64,
    },
    "cross_exchange": {
        "timestamp": pl.Int64,
        "exchange": pl.Utf8,
        "close": pl.Float64,
        "volume": pl.Float64,
    },
    "fx": {
        "timestamp": pl.Int64,
        "pair": pl.Utf8,
        "rate": pl.Float64,
    },
    "records": {
        "timestamp": pl.Int64,
        "side": pl.Utf8,
        "price": pl.Float64,
        "quantity": pl.Float64,
        "fee": pl.Float64,
        "pnl": pl.Float64,
        "source": pl.Utf8,
    },
}


def get_required_columns(data_type: str) -> list[str]:
    """Get required column names for a data type."""
    schema = SCHEMAS.get(data_type)
    if schema is None:
        raise ValueError(f"Unknown data type: {data_type}. Known: {list(SCHEMAS.keys())}")
    return list(schema.keys())


def get_numeric_columns(data_type: str) -> list[str]:
    """Get numeric column names for a data type (for null/NaN checking)."""
    schema = SCHEMAS.get(data_type, {})
    return [
        col for col, dtype in schema.items()
        if dtype in (pl.Float64, pl.Int64, pl.UInt64)
    ]
