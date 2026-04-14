"""Test TimesFM strategy with mock forecast data."""

import polars as pl
import pytest

from bt.strategy.timesfm_signal import TimesFMStrategy


def _make_df_with_forecasts(n: int = 100) -> pl.DataFrame:
    """Create OHLCV + forecast columns with synthetic data."""
    return pl.DataFrame({
        "timestamp": list(range(n)),
        "open": [100.0 + i * 0.1 for i in range(n)],
        "high": [101.0 + i * 0.1 for i in range(n)],
        "low": [99.0 + i * 0.1 for i in range(n)],
        "close": [100.0 + i * 0.1 for i in range(n)],
        "volume": [1000.0] * n,
        "forecast_mean": [100.0 + (i + 24) * 0.1 for i in range(n)],
        "forecast_return": [0.024 if i % 3 != 0 else -0.01 for i in range(n)],
        "forecast_direction": [1.0 if i % 3 != 0 else 0.0 for i in range(n)],
    })


class TestTimesFMStrategy:
    def test_output_shape(self):
        df = _make_df_with_forecasts()
        strategy = TimesFMStrategy(entry_threshold=0.005)
        signals = strategy.compute(df)
        assert len(signals) == len(df)
        assert "strength" in signals.columns
        assert "target_size" in signals.columns

    def test_signals_follow_forecast(self):
        df = _make_df_with_forecasts()
        strategy = TimesFMStrategy(entry_threshold=0.005)
        signals = strategy.compute(df)
        # forecast_return=0.024 > 0.005 -> strength=1.0 for 2/3 of bars
        strengths = signals["strength"].to_list()
        assert sum(s == 1.0 for s in strengths) > len(strengths) * 0.5

    def test_missing_column_raises(self):
        df = pl.DataFrame({"timestamp": [1, 2], "close": [100.0, 101.0]})
        strategy = TimesFMStrategy()
        with pytest.raises(ValueError, match="not found"):
            strategy.compute(df)

    def test_no_nulls_in_output(self):
        df = _make_df_with_forecasts()
        strategy = TimesFMStrategy()
        signals = strategy.compute(df)
        assert signals["strength"].null_count() == 0
        assert signals["target_size"].null_count() == 0
