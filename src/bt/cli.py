"""bt CLI — Typer dispatcher for all subcommands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(name="bt", help="Crypto backtesting framework", no_args_is_help=True)
data_app = typer.Typer(name="data", help="Data management commands", no_args_is_help=True)
app.add_typer(data_app, name="data")


# ── bt data fetch ─────────────────────────────────────────────────────────────

@data_app.command("fetch")
def data_fetch(
    pair: str = typer.Option(..., help="Trading pair (e.g., BTC-USDT, BTC-KRW)"),
    source: str = typer.Option(
        ...,
        help="Source: binance, binance_spot, bithumb, upbit, fx, fng, onchain, binance_vision, coinalyze, coinglass",
    ),
    data_type: str = typer.Option(
        "ohlcv", "--type",
        help="Type: ohlcv, funding, oi, oi_hist, trades, lsr_top_pos, "
             "lsr_top_acct, lsr_global, taker_ratio, depth_snap, liquidations, "
             "metrics, book_depth_hist, fx, fng, onchain",
    ),
    timeframe: str = typer.Option("1h", help="Bar timeframe (1m, 5m, 15m, 1h, 4h, 1d)"),
    start: str = typer.Option("2024-01-01", help="Start date (ISO format)"),
    end: str = typer.Option("2025-01-01", help="End date (ISO format)"),
    output: str = typer.Option(None, help="Output path (auto-generated if omitted)"),
) -> None:
    """Fetch market data from an exchange."""
    from bt.data.fetch import fetch_data

    result = fetch_data(pair, source, data_type, timeframe, start, end, output)
    typer.echo(json.dumps(result, indent=2, default=str))
    if result["status"] != "ok":
        raise typer.Exit(code=1)


# ── bt data list ──────────────────────────────────────────────────────────────

@data_app.command("list")
def data_list() -> None:
    """List all datasets in the data store."""
    from bt.config import find_project_root

    root = find_project_root()
    raw_dir = root / "data" / "raw"
    datasets = []

    if raw_dir.exists():
        for pq in sorted(raw_dir.rglob("*.parquet")):
            rel = pq.relative_to(raw_dir)
            parts = rel.parts
            datasets.append({
                "path": str(pq),
                "relative": str(rel),
                "source": parts[0] if len(parts) > 0 else "unknown",
            })

    typer.echo(json.dumps({"datasets": datasets, "count": len(datasets)}, indent=2))


# ── bt data validate ──────────────────────────────────────────────────────────

@data_app.command("validate")
def data_validate(
    path: str = typer.Option(..., help="Path to data directory or parquet file"),
) -> None:
    """Validate data quality against canonical schemas."""
    from bt.data.validate import validate_path

    report = validate_path(Path(path))
    typer.echo(json.dumps(report, indent=2))
    if not report["valid"]:
        raise typer.Exit(code=1)


# ── bt data import ────────────────────────────────────────────────────────────

@data_app.command("import")
def data_import(
    path: str = typer.Option(..., help="Path to CSV or Parquet file"),
    data_type: str = typer.Option(..., "--type", help="Data type (ohlcv, funding, records, etc.)"),
    output: str = typer.Option(None, help="Output path (auto-generated if omitted)"),
) -> None:
    """Import CSV/Parquet data into the data store."""
    from bt.data.import_ import import_file

    result = import_file(Path(path), data_type, Path(output) if output else None)
    typer.echo(json.dumps(result, indent=2))


# ── bt collect-liq ────────────────────────────────────────────────────────────

@app.command("collect-liq")
def collect_liq(
    flush: float = typer.Option(60.0, help="Flush interval in seconds"),
) -> None:
    """Stream Binance liquidations and append daily Parquet partitions.

    Long-running daemon. Run under a process supervisor. Live-only because
    Binance removed historical liquidations REST in 2021.
    """
    import asyncio

    from bt.config import find_project_root
    from bt.data.collect_liq import collect

    typer.echo("Starting liquidation collector. Ctrl-C to stop.")
    asyncio.run(collect(find_project_root(), flush_interval=flush))


# ── bt features build ─────────────────────────────────────────────────────────

@app.command("features")
def features_build(
    config: str = typer.Option(..., "--config", help="Path to features.yaml"),
) -> None:
    """Build feature DataFrame from multiple data sources."""
    from bt.features.build import build_features

    result = build_features(Path(config))
    typer.echo(json.dumps(result, indent=2))


# ── bt backtest ───────────────────────────────────────────────────────────────

@app.command("backtest")
def backtest(
    config: str = typer.Option(..., "--config", help="Path to strategy config YAML"),
) -> None:
    """Run a backtest. Config is single source of truth."""
    from bt.backtest import run_backtest

    result = run_backtest(Path(config))
    typer.echo(json.dumps(result, indent=2, default=str))


# ── bt report ─────────────────────────────────────────────────────────────────

@app.command("report")
def report(
    run_id: str = typer.Option(..., "--run-id", help="Run ID to report on"),
) -> None:
    """Generate a report for a backtest run."""
    from bt.report.generate import generate_report

    result = generate_report(run_id)
    typer.echo(json.dumps(result, indent=2, default=str))


# ── bt optimize ───────────────────────────────────────────────────────────────

@app.command("optimize")
def optimize(
    config: str = typer.Option(..., "--config", help="Base config YAML"),
    space: str = typer.Option(..., "--space", help="Search space YAML"),
    method: str = typer.Option("grid", "--method", help="Optimization method: grid or random"),
    budget: int = typer.Option(100, "--budget", help="Max number of evaluations"),
) -> None:
    """Run parameter optimization over a search space."""
    from bt.optimize.run import run_optimize

    result = run_optimize(Path(config), Path(space), method, budget)
    typer.echo(json.dumps(result, indent=2, default=str))


# ── bt strategy list ──────────────────────────────────────────────────────────

@app.command("strategies")
def strategy_list() -> None:
    """List registered strategies."""
    from bt.strategy.registry import STRATEGY_REGISTRY

    strategies = [{"name": name, "class": cls.__name__} for name, cls in STRATEGY_REGISTRY.items()]
    typer.echo(json.dumps(strategies, indent=2))


# ── bt train ─────────────────────────────────────────────────────────────

@app.command("train")
def train(
    data_path: str = typer.Option(..., help="Path to feature parquet"),
    output: str = typer.Option("models/ppo", help="Output directory for model"),
    timesteps: int = typer.Option(100_000, help="Total training timesteps"),
    seed: int = typer.Option(42, help="Random seed"),
) -> None:
    """Train a PPO agent on historical data."""
    from bt.rl.train import train_ppo
    result = train_ppo(data_path, output, timesteps, seed=seed)
    typer.echo(json.dumps(result, indent=2, default=str))


# ── bt scan-backtest ─────────────────────────────────────────────────────────

@app.command("scan-backtest")
def scan_backtest(
    name: str = typer.Option("all", help="Scanner name or 'all'"),
    feature_path: str = typer.Option(..., "--features", help="Path to feature parquet"),
    output: str = typer.Option("experiments/scanners", help="Output directory"),
    params: str = typer.Option("{}", help="JSON params dict, keyed by scanner name"),
) -> None:
    """Run scanner backtest(s) against feature parquet."""
    from dataclasses import asdict

    from bt.scanners.registry import get_scanner
    from bt.scanners.run import params_for, run_all, run_scanner

    params_dict = json.loads(params)
    if name == "all":
        results = run_all(feature_path, params_dict, output)
    else:
        spec = get_scanner(name)
        results = [run_scanner(name, feature_path, params_for(params_dict, spec.name), output)]

    typer.echo(json.dumps([asdict(result) for result in results], indent=2, default=str))


# ── bt replay ───────────────────────────────────────────────────────────

@app.command("replay")
def replay_cmd(
    log: str = typer.Option(..., help="Path to trades.jsonl"),
    ohlcv_dir: str = typer.Option("data/raw", help="OHLCV data directory"),
    output: str = typer.Option(None, help="Output JSON path"),
) -> None:
    """Run scan replay optimization — find best entry filters + exit params."""
    from bt.replay.optimize import full_optimize

    result = full_optimize(log, ohlcv_dir, output)
    typer.echo(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    app()
