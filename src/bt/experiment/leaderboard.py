"""Atomic read/write of leaderboard.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

from bt.config import resolve_path

LEADERBOARD_PATH = "experiments/leaderboard.json"


def load_leaderboard() -> dict:
    """Load leaderboard, creating a default if it doesn't exist."""
    path = resolve_path(LEADERBOARD_PATH)
    if not path.exists():
        return {
            "best_run_id": None,
            "best_sharpe": float("-inf"),
            "best_metrics": None,
            "total_runs": 0,
            "feasible_runs": 0,
            "history": [],
        }
    return json.loads(path.read_text())


def save_leaderboard(data: dict) -> None:
    """Atomic save: write to .tmp, then rename."""
    path = resolve_path(LEADERBOARD_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    os.rename(str(tmp_path), str(path))


def update_leaderboard(
    run_id: str,
    metrics: dict,
    feasible: bool,
    reason: str | None = None,
) -> dict:
    """Update leaderboard with a new run result.

    Returns the updated leaderboard.
    """
    lb = load_leaderboard()

    # Add to history
    entry: dict = {"run_id": run_id, "sharpe": metrics.get("sharpe_ratio", 0), "feasible": feasible}
    if reason:
        entry["reason"] = reason
    lb["history"].append(entry)
    lb["total_runs"] = len(lb["history"])

    if feasible:
        lb["feasible_runs"] = sum(1 for h in lb["history"] if h["feasible"])

        sharpe = metrics.get("sharpe_ratio", 0)
        if lb["best_sharpe"] is None or sharpe > lb["best_sharpe"]:
            lb["best_run_id"] = run_id
            lb["best_sharpe"] = sharpe
            lb["best_metrics"] = metrics

    save_leaderboard(lb)
    return lb
