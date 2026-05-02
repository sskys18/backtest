"""Optuna tuning for APPROXIMATION scanners.

Builds forward-return objective and runs TPE search over scanner params.
Best params saved as YAML under experiments/scanner_tuning/.

Excludes squeeze_hunter (AND-gate requires liq data).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import yaml

from bt.optimize.bayesian import BayesianOptimizer
from bt.scanners.registry import get_scanner


# Per-scanner search space. Numeric ranges chosen to bracket defaults ±50%.
SEARCH_SPACES: dict[str, dict] = {
    "alpha_hunter": {
        "entry_stage": {"values": [1, 2, 3]},
    },
    "alpha_terminal": {
        "score_threshold": {"min": 30, "max": 80, "step": 5},
        "w_vwap": {"min": 10, "max": 40, "step": 5},
        "w_rs": {"min": 10, "max": 40, "step": 5},
        "w_liq": {"min": 0, "max": 30, "step": 5},
        "w_oi": {"min": 5, "max": 30, "step": 5},
        "w_fund": {"min": 5, "max": 30, "step": 5},
    },
    "nahonja": {
        "score_threshold": {"min": 30, "max": 80, "step": 4},
    },
    "squeeze_hunter": {
        "oi_velocity_min": {"min": -50.0, "max": 50.0, "step": 5.0},
        "cvd_min": {"min": -100.0, "max": 100.0, "step": 10.0},
        "liq_z_min": {"min": 0.5, "max": 3.5, "step": 0.5},
        "lsr_threshold": {"min": 0.8, "max": 2.0, "step": 0.1},
    },
}


@dataclass
class TuneResult:
    name: str
    best_value: float
    best_params: dict
    n_trials: int
    n_signals_at_best: int
    fwd_horizon_bars: int


def _forward_return_score(
    df: pl.DataFrame, signal_col: str, horizon: int,
    tp_pct: float, sl_pct: float,
    min_signals: int = 20, max_signal_rate: float = 0.15,
) -> tuple[float, int]:
    """Score a signal column by Sharpe of forward returns with TP/SL clamp.

    Returns (sharpe_score, n_signals).
    Penalize:
      - too few signals (<min_signals): score = 0
      - too many signals (signal_rate > max_signal_rate): heavy penalty
        (rewards selectivity; degenerate "always signal" policies score badly)
    """
    df = df.with_columns(pl.col("close").shift(-horizon).alias("_fwd_close"))
    valid = df.filter(pl.col("_fwd_close").is_not_null())
    sig = valid.filter(pl.col(signal_col) > 0)
    n = len(sig)
    n_total = max(len(valid), 1)
    if n < min_signals:
        return 0.0, n

    signal_rate = n / n_total
    if signal_rate > max_signal_rate:
        return -signal_rate, n  # heavy penalty for non-selective signals

    raw_ret = (sig["_fwd_close"] - sig["close"]) / sig["close"]
    clamped = raw_ret.clip(-sl_pct, tp_pct)
    mean = clamped.mean()
    std = clamped.std()
    if std is None or std == 0 or mean is None:
        return 0.0, n
    return float(mean / (std + 1e-6)), n


def _objective(
    spec, df: pl.DataFrame, params: dict, horizon: int, tp: float, sl: float,
) -> float:
    out = spec.compute(df, params)
    score, _ = _forward_return_score(out, "signal", horizon, tp, sl)
    return score


def tune_scanner(
    name: str, feature_path: str, trials: int = 200,
    horizon: int = 12, tp_pct: float = 0.02, sl_pct: float = 0.01,
    train_split: float = 0.7,
) -> TuneResult:
    """Run Optuna TPE over scanner params, score by forward-return Sharpe-like."""
    if name not in SEARCH_SPACES:
        raise ValueError(f"No search space for {name}. Known: {list(SEARCH_SPACES.keys())}")

    spec = get_scanner(name)
    df_full = pl.read_parquet(feature_path)
    missing = [c for c in spec.required_columns if c not in df_full.columns]
    if missing:
        raise RuntimeError(f"{name}: feature parquet missing required columns: {missing}")

    # Train/test split (time-ordered)
    cut = int(len(df_full) * train_split)
    df_train = df_full.head(cut)

    optimizer = BayesianOptimizer(seed=42)

    def objective(params):
        return _objective(spec, df_train, params, horizon, tp_pct, sl_pct)

    result = optimizer.optimize(objective, SEARCH_SPACES[name], budget=trials)

    out_full = spec.compute(df_full, result["best_config"])
    n_signals = int((out_full["signal"] > 0).sum())

    return TuneResult(
        name=name, best_value=float(result["best_score"]),
        best_params=result["best_config"], n_trials=trials,
        n_signals_at_best=n_signals, fwd_horizon_bars=horizon,
    )


def tune_all(
    feature_path: str, output_dir: str, trials: int = 200,
    horizon: int = 12, tp_pct: float = 0.02, sl_pct: float = 0.01,
) -> list[TuneResult]:
    """Tune all scanners with defined search spaces. Save YAML per scanner."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name in SEARCH_SPACES:
        try:
            r = tune_scanner(name, feature_path, trials, horizon, tp_pct, sl_pct)
            results.append(r)
            yaml_path = out_dir / f"{name}_best.yaml"
            with open(yaml_path, "w") as f:
                yaml.dump({
                    "scanner": name,
                    "best_params": r.best_params,
                    "best_score": r.best_value,
                    "n_signals": r.n_signals_at_best,
                    "n_trials": r.n_trials,
                    "fwd_horizon_bars": r.fwd_horizon_bars,
                    "tp_pct": tp_pct, "sl_pct": sl_pct,
                }, f)
        except Exception as exc:
            results.append(TuneResult(
                name=name, best_value=0.0, best_params={}, n_trials=0,
                n_signals_at_best=0, fwd_horizon_bars=horizon,
            ))
            print(f"[{name}] ERROR: {exc}")
    return results
