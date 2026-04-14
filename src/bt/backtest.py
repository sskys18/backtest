"""Backtest orchestrator — loads config, runs strategy, calls Rust engine, saves results."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

from bt.config import Config, load_config, resolve_path
from bt.strategy.base import create_strategy


def run_backtest(config_path: Path) -> dict:
    """Run a full backtest from a YAML config file.

    Steps:
    1. Load config (Pydantic validation)
    2. Load features.parquet
    3. Validate columns
    4. Run strategy.compute()
    5. Validate signals
    6. Pass to Rust engine
    7. Save results
    8. Return metrics
    """
    cfg = load_config(config_path)
    run_id = _generate_run_id()
    run_dir = resolve_path(f"experiments/runs/{run_id}")
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load features
    data_path = resolve_path(cfg.backtest.data_path)
    if not data_path.exists():
        raise FileNotFoundError(f"Data file not found: {data_path}")
    df = pl.read_parquet(data_path)

    # 2. Validate required columns
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Feature DataFrame missing required columns: {missing}")

    # 3. Run strategy
    strategy = create_strategy(cfg.strategy.name, cfg.strategy.params)
    signals_df = strategy.compute(df)

    if len(signals_df) != len(df):
        raise ValueError(
            f"Strategy output length ({len(signals_df)}) != input length ({len(df)})"
        )

    # 4. Validate signals — replace NaN/null with 0.0
    strength = signals_df["strength"].fill_null(0.0).fill_nan(0.0)
    target_size = signals_df["target_size"].fill_null(0.0).fill_nan(0.0)

    null_count = signals_df["strength"].null_count() + signals_df["strength"].is_nan().sum()
    if null_count > 0:
        import sys
        print(f"Warning: {null_count} null/NaN signal values replaced with 0.0", file=sys.stderr)

    # 5. Call Rust engine
    from bt.bt_engine import BacktestEngine

    engine = BacktestEngine(
        initial_capital=cfg.backtest.initial_capital,
        broker={
            "slippage_bps": cfg.broker.slippage_bps,
            "fee_rate": cfg.broker.fee_rate,
            "fill_model": cfg.broker.fill_model,
        },
        risk={
            "max_position_size": cfg.risk.max_position_size,
            "max_drawdown_halt": cfg.risk.max_drawdown_halt,
            "signal_threshold": cfg.risk.signal_threshold,
        },
        bars_per_year=cfg.backtest.bars_per_year,
    )

    result = engine.run(
        timestamps=df["timestamp"].to_numpy().astype(np.int64),
        opens=df["open"].to_numpy().astype(np.float64),
        highs=df["high"].to_numpy().astype(np.float64),
        lows=df["low"].to_numpy().astype(np.float64),
        closes=df["close"].to_numpy().astype(np.float64),
        volumes=df["volume"].to_numpy().astype(np.float64),
        strengths=strength.to_numpy().astype(np.float64),
        target_sizes=target_size.to_numpy().astype(np.float64),
    )

    # 6. Save results
    _save_results(run_id, run_dir, config_path, cfg, data_path, result)

    # 7. Return metrics with run_id
    metrics = dict(result["metrics"])
    metrics["run_id"] = run_id
    return metrics


def _generate_run_id() -> str:
    """Generate a run ID: YYYY-MM-DD_NNN."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    runs_dir = resolve_path("experiments/runs")
    runs_dir.mkdir(parents=True, exist_ok=True)

    existing = [d.name for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith(today)]
    seq = len(existing) + 1
    return f"{today}_{seq:03d}"


def _save_results(
    run_id: str,
    run_dir: Path,
    config_path: Path,
    cfg: Config,
    data_path: Path,
    result: dict,
) -> None:
    """Save metrics, config, equity curve, and manifest."""
    # Metrics
    metrics = dict(result["metrics"])
    metrics["run_id"] = run_id
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))

    # Frozen config
    shutil.copy2(config_path, run_dir / "config.yaml")

    # Equity curve
    eq_ts = result["equity_timestamps"]
    eq_vals = result["equity_values"]
    eq_df = pl.DataFrame({
        "timestamp": eq_ts,
        "equity": eq_vals,
    })
    eq_df.write_parquet(run_dir / "equity_curve.parquet")

    # Manifest
    manifest = _build_manifest(run_id, cfg, data_path, config_path)
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


def _build_manifest(
    run_id: str, cfg: Config, data_path: Path, config_path: Path,
) -> dict:
    """Build a reproducibility manifest."""
    manifest: dict = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": cfg.model_dump(),
        "data_path": str(data_path),
    }

    # Data hash
    if data_path.exists():
        h = hashlib.sha256(data_path.read_bytes()).hexdigest()
        manifest["data_hash"] = f"sha256:{h}"

    # Git commit
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        manifest["git_commit"] = commit
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Version info
    import sys
    manifest["python_version"] = sys.version.split()[0]

    return manifest
