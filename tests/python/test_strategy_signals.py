"""Test strategy signal computation."""

import polars as pl
import pytest

from bt.strategy.momentum import MomentumStrategy
from bt.strategy.mean_revert import MeanRevertStrategy


def _make_ohlcv(closes: list[float]) -> pl.DataFrame:
    """Create a simple OHLCV DataFrame from close prices."""
    return pl.DataFrame({
        "timestamp": list(range(len(closes))),
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [100.0] * len(closes),
    })


class TestMomentumStrategy:
    def test_output_shape(self):
        df = _make_ohlcv([100.0] * 50)
        strategy = MomentumStrategy(fast_period=5, slow_period=10)
        signals = strategy.compute(df)
        assert len(signals) == len(df)
        assert "strength" in signals.columns
        assert "target_size" in signals.columns

    def test_no_nulls_in_output(self):
        df = _make_ohlcv([100.0 + i for i in range(50)])
        strategy = MomentumStrategy(fast_period=5, slow_period=10)
        signals = strategy.compute(df)
        assert signals["strength"].null_count() == 0
        assert signals["target_size"].null_count() == 0

    def test_uptrend_generates_long_signals(self):
        # Steadily rising prices — fast MA > slow MA after warmup
        closes = [100.0 + i * 2.0 for i in range(50)]
        df = _make_ohlcv(closes)
        strategy = MomentumStrategy(fast_period=5, slow_period=10, position_size=0.15)
        signals = strategy.compute(df)
        # After warmup, should have strength = 1.0
        late_signals = signals["strength"].to_list()[-20:]
        assert all(s == 1.0 for s in late_signals)

    def test_position_size_is_constant(self):
        df = _make_ohlcv([100.0] * 30)
        strategy = MomentumStrategy(position_size=0.25)
        signals = strategy.compute(df)
        assert all(v == 0.25 for v in signals["target_size"].to_list())


class TestMeanRevertStrategy:
    def test_output_shape(self):
        df = _make_ohlcv([100.0] * 50)
        strategy = MeanRevertStrategy(bb_period=10)
        signals = strategy.compute(df)
        assert len(signals) == len(df)
        assert "strength" in signals.columns
        assert "target_size" in signals.columns

    def test_no_nulls(self):
        df = _make_ohlcv([100.0 + (i % 10) for i in range(50)])
        strategy = MeanRevertStrategy(bb_period=10)
        signals = strategy.compute(df)
        assert signals["strength"].null_count() == 0
