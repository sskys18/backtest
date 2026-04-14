"""Normalize and store data as Parquet."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from bt.data.schemas import SCHEMAS


def normalize_and_save(
    df: pl.DataFrame,
    data_type: str,
    output_path: Path,
) -> Path:
    """Cast columns to canonical types and save as Parquet.

    Returns the output path.
    """
    schema = SCHEMAS.get(data_type)
    if schema is None:
        raise ValueError(f"Unknown data type: {data_type}")

    # Cast columns to canonical types
    cast_exprs = []
    for col, dtype in schema.items():
        if col in df.columns:
            cast_exprs.append(pl.col(col).cast(dtype))

    if cast_exprs:
        df = df.select(cast_exprs)

    # Sort by timestamp
    if "timestamp" in df.columns:
        df = df.sort("timestamp")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df.write_parquet(output_path)
    return output_path
