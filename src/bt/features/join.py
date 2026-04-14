"""Multi-source join logic for feature building."""

from __future__ import annotations

from pathlib import Path

import polars as pl


def load_parquet_dir(path: Path) -> pl.DataFrame:
    """Load all parquet files in a directory and concatenate."""
    files = sorted(path.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files found in {path}")
    dfs = [pl.read_parquet(f) for f in files]
    return pl.concat(dfs).sort("timestamp")


def load_source(path: str) -> pl.DataFrame:
    """Load a source from path (file or directory)."""
    p = Path(path)
    if p.is_file():
        return pl.read_parquet(p)
    elif p.is_dir():
        return load_parquet_dir(p)
    else:
        raise FileNotFoundError(f"Source path not found: {path}")


def resample_forward_fill(df: pl.DataFrame, backbone_ts: pl.Series) -> pl.DataFrame:
    """Resample a DataFrame to match backbone timestamps using forward fill."""
    # Create a skeleton with backbone timestamps
    skeleton = pl.DataFrame({"timestamp": backbone_ts})

    # Join and sort
    joined = skeleton.join(df, on="timestamp", how="left").sort("timestamp")

    # Forward fill all non-timestamp columns
    fill_cols = [col for col in joined.columns if col != "timestamp"]
    return joined.with_columns([pl.col(c).forward_fill() for c in fill_cols])


def aggregate_records(df: pl.DataFrame, interval_ms: int) -> pl.DataFrame:
    """Aggregate trading records into bar-sized bins."""
    # Floor timestamps to interval
    df = df.with_columns(
        (pl.col("timestamp") // interval_ms * interval_ms).alias("timestamp")
    )

    return df.group_by("timestamp").agg([
        pl.len().alias("trade_count"),
        (
            pl.when(pl.col("side") == "buy").then(pl.col("quantity")).otherwise(-pl.col("quantity"))
        ).sum().alias("net_volume"),
        pl.col("pnl").sum().alias("total_pnl"),
    ]).sort("timestamp")


def join_sources(
    backbone: pl.DataFrame,
    sources: list[dict],
) -> pl.DataFrame:
    """Join multiple data sources onto the OHLCV backbone by timestamp.

    Each source dict has: path, type, resample (optional), prefix (optional),
    aggregate (optional), columns (optional).
    """
    result = backbone.clone()

    for source in sources:
        src_df = load_source(source["path"])
        resample = source.get("resample")
        prefix = source.get("prefix", "")
        agg = source.get("aggregate")

        if agg:
            # Aggregate trading records
            interval_map = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
            interval_ms = interval_map.get(agg, 3_600_000)
            src_df = aggregate_records(src_df, interval_ms)

        # Rename columns with prefix (except timestamp)
        if prefix:
            rename_map = {col: f"{prefix}{col}" for col in src_df.columns if col != "timestamp"}
            src_df = src_df.rename(rename_map)

        if resample == "forward_fill":
            src_df = resample_forward_fill(src_df, result["timestamp"])
            # After resampling, src_df has the same timestamps as backbone
            # Drop timestamp from src_df before hstacking
            non_ts_cols = [c for c in src_df.columns if c != "timestamp"]
            result = result.hstack(src_df.select(non_ts_cols))
        else:
            # Left join
            result = result.join(src_df, on="timestamp", how="left")

    return result
