import numpy as np
import pytest


def test_engine_basic_round_trip():
    from bt.bt_engine import BacktestEngine

    engine = BacktestEngine(
        initial_capital=10_000.0,
        broker={"slippage_bps": 0.0, "fee_rate": 0.0, "fill_model": "immediate"},
        risk={"max_position_size": 1.0, "max_drawdown_halt": 0.5, "signal_threshold": 0.5},
        bars_per_year=8760.0,
    )

    n = 5
    timestamps = np.arange(1, n + 1, dtype=np.int64) * 3_600_000
    closes = np.array([100.0, 110.0, 120.0, 130.0, 120.0])
    opens = closes.copy()
    highs = closes.copy()
    lows = closes.copy()
    volumes = np.full(n, 100.0)

    strengths = np.array([1.0, 1.0, 1.0, 0.0, 0.0])
    target_sizes = np.array([1.0, 1.0, 1.0, 0.0, 0.0])

    result = engine.run(
        timestamps=timestamps, opens=opens, highs=highs, lows=lows,
        closes=closes, volumes=volumes, strengths=strengths, target_sizes=target_sizes,
    )

    assert result["metrics"]["num_round_trips"] == 1
    assert result["metrics"]["total_return"] == pytest.approx(0.3, abs=1e-6)
    assert len(result["fills"]) == 2
    assert result["fills"][0]["side"] == "buy"
    assert result["fills"][1]["side"] == "sell"
    assert result["halted_at"] is None


def test_engine_empty_signals_no_trades():
    from bt.bt_engine import BacktestEngine

    engine = BacktestEngine(
        initial_capital=10_000.0,
        broker={"slippage_bps": 5.0, "fee_rate": 0.001, "fill_model": "next_bar"},
        risk={"max_position_size": 0.2, "max_drawdown_halt": 0.15, "signal_threshold": 0.5},
        bars_per_year=8760.0,
    )

    n = 3
    timestamps = np.arange(1, n + 1, dtype=np.int64) * 3_600_000
    prices = np.array([100.0, 101.0, 102.0])
    zeros = np.zeros(n)

    result = engine.run(
        timestamps=timestamps, opens=prices, highs=prices, lows=prices,
        closes=prices, volumes=np.full(n, 100.0), strengths=zeros, target_sizes=zeros,
    )

    assert result["metrics"]["num_round_trips"] == 0
    assert result["metrics"]["total_return"] == pytest.approx(0.0)
    assert result["final_state"]["equity"] == pytest.approx(10_000.0)


def test_engine_length_mismatch():
    from bt.bt_engine import BacktestEngine

    engine = BacktestEngine(
        initial_capital=10_000.0,
        broker={"slippage_bps": 0.0, "fee_rate": 0.0, "fill_model": "immediate"},
        risk={"max_position_size": 1.0, "max_drawdown_halt": 0.5, "signal_threshold": 0.5},
        bars_per_year=8760.0,
    )

    with pytest.raises(ValueError, match="length"):
        engine.run(
            timestamps=np.array([1], dtype=np.int64),
            opens=np.array([100.0]),
            highs=np.array([100.0]),
            lows=np.array([100.0]),
            closes=np.array([100.0]),
            volumes=np.array([100.0]),
            strengths=np.array([0.0, 0.0]),  # wrong length
            target_sizes=np.array([0.0, 0.0]),
        )
