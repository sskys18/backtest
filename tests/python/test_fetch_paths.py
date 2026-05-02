"""Tests for fetch output path layout."""
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest


def _stub_source(df):
    class S:
        def supported_types(self):
            return [
                "ohlcv", "funding", "oi", "oi_hist", "trades",
                "lsr_top_pos", "taker_ratio", "depth_snap", "metrics",
                "book_depth_hist", "fng", "onchain",
            ]

        async def fetch(self, **kw):
            return df
    return S()


@pytest.mark.parametrize("dtype,timeframe,expect_tf_segment", [
    ("ohlcv", "1h", True),
    ("trades", "5m", True),
    ("oi_hist", "5m", True),
    ("lsr_top_pos", "1h", True),
    ("taker_ratio", "1h", True),
    ("metrics", "1m", True),
    ("book_depth_hist", "1m", True),
    ("funding", "1h", False),
    ("oi", "1h", False),
    ("depth_snap", "1m", False),
    ("fng", "1d", False),
    ("onchain", "1d", False),
])
def test_fetch_path_layout(tmp_path, dtype, timeframe, expect_tf_segment):
    from bt.data import fetch as fetch_mod
    if dtype == "ohlcv":
        df = pl.DataFrame({
            "timestamp": [1000], "open": [1.0], "high": [2.0], "low": [0.5],
            "close": [1.5], "volume": [100.0],
        })
    else:
        from bt.data.schemas import SCHEMAS
        df = pl.DataFrame({
            c: [0] if d in (pl.Int64, pl.UInt64)
            else [0.0] if d == pl.Float64
            else ["x"]
            for c, d in SCHEMAS[dtype].items()
        })

    with patch.object(fetch_mod, "_get_source", return_value=_stub_source(df)), \
         patch.object(fetch_mod, "find_project_root", return_value=tmp_path), \
         patch.object(fetch_mod, "validate_dataframe",
                      return_value={"valid": True, "errors": [], "warnings": [], "rows": 1}):
        result = fetch_mod.fetch_data(
            pair="BTC-USDT", source="binance", data_type=dtype,
            timeframe=timeframe, start="2024-01-01", end="2024-01-02",
        )
    path = Path(result["path"])
    parts = path.parts
    if expect_tf_segment:
        assert timeframe in parts, f"{dtype} should include {timeframe} in {parts}"
    else:
        assert timeframe not in parts[parts.index(dtype):], (
            f"{dtype} should NOT include {timeframe} after type segment in {parts}"
        )
