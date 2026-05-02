"""bt data fetch — pull market data from exchanges."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from bt.config import find_project_root
from bt.data.store import normalize_and_save
from bt.data.validate import validate_dataframe


TIMEFRAMED_TYPES = frozenset({
    "ohlcv", "trades", "oi_hist",
    "lsr_top_pos", "lsr_top_acct", "lsr_global", "taker_ratio",
    "metrics", "book_depth_hist",
})

# Source registry — lazy imports to avoid requiring all exchange deps
SOURCE_REGISTRY: dict[str, str] = {
    "binance": "bt.data.sources.binance:BinanceSource",
    "binance_spot": "bt.data.sources.binance:BinanceSpotSource",
    "bithumb": "bt.data.sources.bithumb:BithumbSource",
    "upbit": "bt.data.sources.upbit:UpbitSource",
    "fx": "bt.data.sources.fx:FxSource",
    "fng": "bt.data.sources.fng:FngSource",
    "onchain": "bt.data.sources.onchain:OnchainSource",
    "binance_vision": "bt.data.sources.binance_vision:BinanceVisionSource",
    "coinalyze": "bt.data.sources.coinalyze:CoinalyzeSource",
    "coinglass": "bt.data.sources.coinglass:CoinglassSource",
    "coinank": "bt.data.sources.coinank:CoinankSource",
}


def _get_source(name: str):
    """Lazy-load a data source by name."""
    entry = SOURCE_REGISTRY.get(name)
    if entry is None:
        raise ValueError(f"Unknown source: {name}. Known: {list(SOURCE_REGISTRY.keys())}")

    module_path, class_name = entry.rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def fetch_data(
    pair: str,
    source: str,
    data_type: str,
    timeframe: str = "1h",
    start: str = "2024-01-01",
    end: str = "2025-01-01",
    output: str | None = None,
) -> dict:
    """Fetch market data from an exchange and save as Parquet.

    Returns a result dict with status, path, and row count.
    """
    src = _get_source(source)

    # Validate data type
    supported = src.supported_types()
    if data_type not in supported:
        return {
            "status": "error",
            "message": f"Source '{source}' does not support '{data_type}'. Supported: {supported}",
        }

    # Parse dates
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    # Fetch
    df = asyncio.run(src.fetch(
        pair=pair,
        data_type=data_type,
        timeframe=timeframe,
        start=start_dt,
        end=end_dt,
    ))

    if len(df) == 0:
        return {"status": "error", "message": "No data returned"}

    # Validate
    report = validate_dataframe(df, data_type)
    if not report["valid"]:
        # Try to proceed anyway with warnings
        pass

    # Determine output path
    if output:
        out_path = Path(output)
    else:
        root = find_project_root()
        pair_dir = pair.replace("/", "-")
        base = root / "data" / "raw" / source / pair_dir / data_type
        if data_type in TIMEFRAMED_TYPES:
            out_path = base / timeframe / "data.parquet"
        else:
            out_path = base / "data.parquet"

    saved = normalize_and_save(df, data_type, out_path)

    return {
        "status": "ok",
        "path": str(saved),
        "rows": len(df),
        "source": source,
        "pair": pair,
        "type": data_type,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "validation": report,
    }
