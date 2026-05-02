"""Test data validation."""

import polars as pl
import pytest

from bt.data.validate import validate_dataframe


class TestOHLCVValidation:
    def test_valid_ohlcv(self):
        df = pl.DataFrame({
            "timestamp": [1000, 2000, 3000],
            "open": [100.0, 101.0, 102.0],
            "high": [105.0, 106.0, 107.0],
            "low": [95.0, 96.0, 97.0],
            "close": [102.0, 103.0, 104.0],
            "volume": [1000.0, 1100.0, 1200.0],
        })
        report = validate_dataframe(df, "ohlcv")
        assert report["valid"]
        assert len(report["errors"]) == 0

    def test_non_monotonic_timestamps(self):
        df = pl.DataFrame({
            "timestamp": [1000, 3000, 2000],  # out of order
            "open": [100.0, 101.0, 102.0],
            "high": [105.0, 106.0, 107.0],
            "low": [95.0, 96.0, 97.0],
            "close": [102.0, 103.0, 104.0],
            "volume": [1000.0, 1100.0, 1200.0],
        })
        report = validate_dataframe(df, "ohlcv")
        assert not report["valid"]
        assert any("monotonically" in e for e in report["errors"])

    def test_ohlc_violation_high(self):
        df = pl.DataFrame({
            "timestamp": [1000, 2000],
            "open": [100.0, 101.0],
            "high": [99.0, 106.0],  # high < open on first row
            "low": [95.0, 96.0],
            "close": [102.0, 103.0],
            "volume": [1000.0, 1100.0],
        })
        report = validate_dataframe(df, "ohlcv")
        assert not report["valid"]
        assert any("OHLC violation" in e for e in report["errors"])

    def test_negative_volume(self):
        df = pl.DataFrame({
            "timestamp": [1000, 2000],
            "open": [100.0, 101.0],
            "high": [105.0, 106.0],
            "low": [95.0, 96.0],
            "close": [102.0, 103.0],
            "volume": [1000.0, -100.0],  # negative
        })
        report = validate_dataframe(df, "ohlcv")
        assert not report["valid"]

    def test_empty_dataframe(self):
        df = pl.DataFrame({
            "timestamp": pl.Series([], dtype=pl.Int64),
            "open": pl.Series([], dtype=pl.Float64),
            "high": pl.Series([], dtype=pl.Float64),
            "low": pl.Series([], dtype=pl.Float64),
            "close": pl.Series([], dtype=pl.Float64),
            "volume": pl.Series([], dtype=pl.Float64),
        })
        report = validate_dataframe(df, "ohlcv")
        assert not report["valid"]


class TestExtendedSchemas:
    def test_oi_hist_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "oi_hist" in SCHEMAS
        assert get_required_columns("oi_hist") == [
            "timestamp", "open_interest", "open_interest_value",
        ]

    def test_lsr_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        for t in ("lsr_top_pos", "lsr_top_acct", "lsr_global"):
            assert t in SCHEMAS
            cols = get_required_columns(t)
            assert cols == ["timestamp", "long_short_ratio", "long_account", "short_account"]

    def test_taker_ratio_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "taker_ratio" in SCHEMAS
        assert get_required_columns("taker_ratio") == [
            "timestamp", "buy_sell_ratio", "buy_vol", "sell_vol",
        ]

    def test_depth_snap_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "depth_snap" in SCHEMAS
        cols = get_required_columns("depth_snap")
        assert cols[0] == "timestamp"
        assert "bid_total_qty" in cols
        assert "ask_total_qty" in cols
        assert "imbalance" in cols

    def test_fng_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "fng" in SCHEMAS
        assert get_required_columns("fng") == [
            "timestamp", "value", "classification",
        ]

    def test_onchain_schema(self):
        from bt.data.schemas import SCHEMAS, get_required_columns
        assert "onchain" in SCHEMAS
        cols = get_required_columns("onchain")
        assert "timestamp" in cols
        assert "tx_count" in cols
        assert "hashrate" in cols
        assert "fees_usd" in cols

    def test_binance_vision_schemas(self):
        from bt.data.schemas import SCHEMAS, get_required_columns

        assert "metrics" in SCHEMAS
        assert get_required_columns("metrics") == [
            "timestamp",
            "open_interest",
            "open_interest_value",
            "toptrader_lsr",
            "global_lsr",
            "taker_ratio",
        ]
        assert "book_depth_hist" in SCHEMAS
        assert get_required_columns("book_depth_hist") == [
            "timestamp",
            "percentage",
            "depth",
            "notional",
        ]

    def test_validate_path_infers_oi_hist_schema(self, tmp_path):
        from bt.data.validate import validate_path

        path = tmp_path / "oi_hist.parquet"
        pl.DataFrame({
            "timestamp": [1700000000000],
            "open_interest": [10000.5],
            "open_interest_value": [500000000.0],
        }).write_parquet(path)

        report = validate_path(path)

        assert report["valid"]
        assert report["data_type"] == "oi_hist"
