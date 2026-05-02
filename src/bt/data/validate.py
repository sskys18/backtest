"""Data validation against canonical schemas."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from bt.data.schemas import SCHEMAS, get_numeric_columns, get_required_columns


def validate_dataframe(df: pl.DataFrame, data_type: str) -> dict:
    """Validate a DataFrame against its canonical schema.

    Returns a report dict with 'valid', 'errors', 'warnings'.
    """
    errors: list[str] = []
    warnings: list[str] = []

    required = get_required_columns(data_type)
    numeric = get_numeric_columns(data_type)

    # Check required columns exist
    missing = [col for col in required if col not in df.columns]
    if missing:
        errors.append(f"Missing columns: {missing}")
        return {"valid": False, "errors": errors, "warnings": warnings, "rows": len(df)}

    if len(df) == 0:
        errors.append("DataFrame is empty")
        return {"valid": False, "errors": errors, "warnings": warnings, "rows": 0}

    # Shared: timestamps monotonically increasing
    if "timestamp" in df.columns:
        ts = df["timestamp"]
        diffs = ts.diff().drop_nulls()
        if (diffs <= 0).any():
            errors.append("Timestamps are not monotonically increasing")

        # Check for duplicates
        if ts.n_unique() != len(ts):
            errors.append(f"Duplicate timestamps found: {len(ts) - ts.n_unique()} duplicates")

    # Shared: no nulls in numeric columns
    for col in numeric:
        if col in df.columns:
            null_count = df[col].null_count()
            if null_count > 0:
                errors.append(f"Column '{col}' has {null_count} null values")

    # OHLCV-specific validations
    if data_type == "ohlcv":
        _validate_ohlcv(df, errors, warnings)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "rows": len(df),
        "data_type": data_type,
    }


def _validate_ohlcv(df: pl.DataFrame, errors: list[str], warnings: list[str]) -> None:
    """OHLCV-specific validation rules."""
    # OHLC relationship: low <= open, close <= high
    bad_high = (df["high"] < df["open"]).sum() + (df["high"] < df["close"]).sum()
    bad_low = (df["low"] > df["open"]).sum() + (df["low"] > df["close"]).sum()

    if bad_high > 0:
        errors.append(f"OHLC violation: high < open or close in {bad_high} rows")
    if bad_low > 0:
        errors.append(f"OHLC violation: low > open or close in {bad_low} rows")

    # Volume >= 0
    neg_vol = (df["volume"] < 0).sum()
    if neg_vol > 0:
        errors.append(f"Negative volume in {neg_vol} rows")

    # Gap detection (warning only)
    if len(df) > 1:
        ts_diffs = df["timestamp"].diff().drop_nulls()
        median_interval = ts_diffs.median()
        if median_interval and median_interval > 0:
            large_gaps = (ts_diffs > median_interval * 3).sum()
            if large_gaps > 0:
                warnings.append(f"Found {large_gaps} gaps exceeding 3x the median interval")


def validate_path(path: Path) -> dict:
    """Validate a parquet file or directory of parquet files."""
    if path.is_file() and path.suffix == ".parquet":
        df = pl.read_parquet(path)
        # Infer data type from columns
        data_type = _infer_data_type(df)
        report = validate_dataframe(df, data_type)
        report["path"] = str(path)
        return report

    if path.is_dir():
        results = []
        for pq_file in sorted(path.rglob("*.parquet")):
            df = pl.read_parquet(pq_file)
            data_type = _infer_data_type(df)
            r = validate_dataframe(df, data_type)
            r["path"] = str(pq_file)
            results.append(r)
        all_valid = all(r["valid"] for r in results) if results else False
        return {"valid": all_valid, "files": results}

    return {"valid": False, "errors": [f"Path not found or not a parquet file: {path}"]}


def _infer_data_type(df: pl.DataFrame) -> str:
    """Infer data type from DataFrame columns."""
    cols = set(df.columns)
    schemas_by_specificity = sorted(
        SCHEMAS.items(),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    for dtype, schema in schemas_by_specificity:
        required = set(schema.keys())
        if required.issubset(cols):
            return dtype
    # Default to ohlcv if columns match partially
    if {"timestamp", "open", "high", "low", "close"}.issubset(cols):
        return "ohlcv"
    return "unknown"
