"""Test feature indicators."""

import polars as pl
import pytest

from bt.features.indicators import sma, ema, rsi, bollinger


def _price_df(closes: list[float]) -> pl.DataFrame:
    return pl.DataFrame({
        "timestamp": list(range(len(closes))),
        "close": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "volume": [100.0] * len(closes),
    })


class TestSMA:
    def test_basic(self):
        df = _price_df([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(df, "close", 3, "sma_3")
        vals = result["sma_3"].to_list()
        # First 2 are null (not enough data), third = (1+2+3)/3 = 2.0
        assert vals[2] == pytest.approx(2.0)
        assert vals[4] == pytest.approx(4.0)

    def test_output_length(self):
        df = _price_df([float(i) for i in range(100)])
        result = sma(df, "close", 10, "sma_10")
        assert len(result) == 100


class TestEMA:
    def test_basic(self):
        df = _price_df([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ema(df, "close", 3, "ema_3")
        assert len(result) == 5
        # EMA values should exist for all rows
        assert result["ema_3"].null_count() == 0


class TestRSI:
    def test_range(self):
        df = _price_df([100.0 + i for i in range(50)])
        result = rsi(df, "close", 14, "rsi_14")
        # RSI should be between 0 and 100 (excluding warmup nulls)
        non_null = result["rsi_14"].drop_nulls()
        assert non_null.min() >= 0.0
        assert non_null.max() <= 100.0


class TestBollinger:
    def test_three_bands(self):
        df = _price_df([100.0 + i for i in range(30)])
        result = bollinger(df, "close", 20, 2.0, ["bb_upper", "bb_middle", "bb_lower"])
        assert "bb_upper" in result.columns
        assert "bb_middle" in result.columns
        assert "bb_lower" in result.columns

    def test_band_ordering(self):
        df = _price_df([100.0 + i * 0.1 for i in range(30)])
        result = bollinger(df, "close", 20, 2.0, ["bb_upper", "bb_middle", "bb_lower"])
        # After warmup, upper > middle > lower
        row = result.row(25, named=True)
        if row["bb_upper"] is not None:
            assert row["bb_upper"] >= row["bb_middle"]
            assert row["bb_middle"] >= row["bb_lower"]
