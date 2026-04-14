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
