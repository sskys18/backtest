"""End-to-end backtest test with synthetic data."""

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest


@pytest.fixture
def project_root(tmp_path):
    """Create a temporary project structure with synthetic data."""
    # Create directories
    (tmp_path / "data" / "features").mkdir(parents=True)
    (tmp_path / "experiments" / "runs").mkdir(parents=True)
    (tmp_path / "configs" / "strategies").mkdir(parents=True)
    (tmp_path / "pyproject.toml").touch()

    # Generate synthetic OHLCV data — trending up then down
    n = 500
    np.random.seed(42)
    base = 100.0
    returns = np.random.normal(0.0005, 0.02, n)
    prices = base * np.exp(np.cumsum(returns))

    df = pl.DataFrame({
        "timestamp": [int(i * 3_600_000) for i in range(n)],
        "open": prices.tolist(),
        "high": (prices * 1.01).tolist(),
        "low": (prices * 0.99).tolist(),
        "close": prices.tolist(),
        "volume": [1000.0] * n,
    })
    df.write_parquet(tmp_path / "data" / "features" / "BTC-USDT-1h.parquet")

    # Write config
    config = {
        "strategy": {
            "name": "momentum",
            "params": {"fast_period": 12, "slow_period": 26, "position_size": 0.15},
        },
        "backtest": {
            "pair": "BTC-USDT",
            "timeframe": "1h",
            "initial_capital": 10000.0,
            "data_path": "data/features/BTC-USDT-1h.parquet",
        },
        "broker": {"slippage_bps": 5.0, "fee_rate": 0.001, "fill_model": "next_bar"},
        "risk": {"max_position_size": 0.20, "max_drawdown_halt": 0.50, "signal_threshold": 0.5},
    }

    import yaml
    config_path = tmp_path / "configs" / "strategies" / "test_momentum.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    return tmp_path, config_path


def test_e2e_backtest(project_root, monkeypatch):
    """Full end-to-end: config → strategy → engine → results saved."""
    root, config_path = project_root

    # Monkeypatch project root detection
    monkeypatch.chdir(root)

    from bt.backtest import run_backtest

    metrics = run_backtest(config_path)

    assert "run_id" in metrics
    assert "total_return" in metrics
    assert "sharpe_ratio" in metrics
    assert "max_drawdown" in metrics
    assert isinstance(metrics["num_round_trips"], int)

    # Verify run artifacts were saved
    run_dir = root / "experiments" / "runs" / metrics["run_id"]
    assert (run_dir / "metrics.json").exists()
    assert (run_dir / "config.yaml").exists()
    assert (run_dir / "equity_curve.parquet").exists()
    assert (run_dir / "manifest.json").exists()

    # Verify metrics.json is valid
    saved_metrics = json.loads((run_dir / "metrics.json").read_text())
    assert saved_metrics["sharpe_ratio"] == pytest.approx(metrics["sharpe_ratio"])
