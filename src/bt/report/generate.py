"""Report generation — JSON metrics + optional summary."""

from __future__ import annotations

import json
from pathlib import Path

from bt.config import resolve_path
from bt.report.metrics import format_metrics


def generate_report(run_id: str) -> dict:
    """Generate a report for a given run ID.

    Returns the metrics dict augmented with summary info.
    """
    run_dir = resolve_path(f"experiments/runs/{run_id}")

    if not run_dir.exists():
        raise FileNotFoundError(f"Run not found: {run_id}")

    # Load metrics
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"No metrics.json in run {run_id}")

    metrics = json.loads(metrics_path.read_text())

    # Load manifest
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    # Build report
    report = {
        "run_id": run_id,
        "metrics": metrics,
        "manifest": manifest,
        "summary": format_metrics(metrics),
    }

    # Check for equity curve
    eq_path = run_dir / "equity_curve.parquet"
    if eq_path.exists():
        report["equity_curve_path"] = str(eq_path)

    return report
