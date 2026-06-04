"""bt CLI — Typer dispatcher for all subcommands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(name="bt", help="Crypto backtesting framework", no_args_is_help=True)
data_app = typer.Typer(name="data", help="Data management commands", no_args_is_help=True)
app.add_typer(data_app, name="data")


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


# ── bt backtest-panel ─────────────────────────────────────────────────────────

@app.command("backtest-panel")
def backtest_panel(
    panel: str = typer.Option(..., "--panel", help="Path to long-format panel parquet"),
    config: str = typer.Option(..., "--config", help="Path to portfolio panel config YAML"),
) -> None:
    """Run a multi-asset panel backtest (loader → strategy → engine → report)."""
    from bt.panel.report import run_panel_backtest

    report_dict = run_panel_backtest(panel, config)
    typer.echo(json.dumps(report_dict, indent=2, default=str))


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


# ── bt scanner-tune ──────────────────────────────────────────────────────────

@app.command("scanner-tune")
def scanner_tune(
    name: str = typer.Option("all", help="Scanner name or 'all' (alpha_hunter|alpha_terminal|nahonja)"),
    feature_path: str = typer.Option(..., "--features", help="Path to feature parquet"),
    output: str = typer.Option("experiments/scanner_tuning", help="Output directory"),
    trials: int = typer.Option(200, help="Optuna TPE trials per scanner"),
    horizon: int = typer.Option(12, help="Forward-return horizon in bars"),
    tp: float = typer.Option(0.02, help="TP cap as fraction"),
    sl: float = typer.Option(0.01, help="SL cap as fraction"),
) -> None:
    """Tune APPROXIMATION scanners' params via Optuna TPE on forward-return Sharpe."""
    from dataclasses import asdict

    from bt.scanners.tune import tune_all, tune_scanner

    if name == "all":
        results = tune_all(feature_path, output, trials, horizon, tp, sl)
    else:
        results = [tune_scanner(name, feature_path, trials, horizon, tp, sl)]
    typer.echo(json.dumps([asdict(r) for r in results], indent=2, default=str))


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
