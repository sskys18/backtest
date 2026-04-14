"""Optimizer dispatch — runs optimization over a search space."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from bt.backtest import run_backtest
from bt.config import load_config
from bt.optimize.grid import GridSearchOptimizer, RandomSearchOptimizer


def run_optimize(
    config_path: Path,
    space_path: Path,
    method: str,
    budget: int,
) -> dict:
    """Run parameter optimization.

    The search space YAML maps config paths to ranges:
    {
        "strategy.params.fast_period": {"min": 5, "max": 50, "step": 5},
        "strategy.params.position_size": {"values": [0.05, 0.10, 0.15, 0.20]},
    }
    """
    with open(space_path) as f:
        search_space = yaml.safe_load(f)

    with open(config_path) as f:
        base_config_raw = yaml.safe_load(f)

    # Write a temp config for each evaluation and run backtest
    def objective(params: dict) -> float:
        cfg_raw = copy.deepcopy(base_config_raw)
        for path_str, value in params.items():
            _set_nested(cfg_raw, path_str.split("."), value)

        # Write temp config
        tmp_config = config_path.parent / f"_opt_tmp_{id(params)}.yaml"
        try:
            with open(tmp_config, "w") as f:
                yaml.dump(cfg_raw, f)
            metrics = run_backtest(tmp_config)
            return metrics.get("sharpe_ratio", float("-inf"))
        finally:
            tmp_config.unlink(missing_ok=True)

    if method == "random":
        optimizer = RandomSearchOptimizer()
    else:
        optimizer = GridSearchOptimizer()

    return optimizer.optimize(objective, search_space, budget)


def _set_nested(d: dict, keys: list[str], value) -> None:
    """Set a nested dict value by key path."""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value
