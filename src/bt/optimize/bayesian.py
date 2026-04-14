"""Bayesian optimization via Optuna TPE sampler."""

from __future__ import annotations

from typing import Any, Callable

import optuna
from optuna.samplers import TPESampler


class BayesianOptimizer:
    """Optuna-based Bayesian optimizer using Tree-structured Parzen Estimators."""

    def __init__(self, seed: int = 42):
        self.seed = seed

    def optimize(
        self,
        objective: Callable[[dict], float],
        search_space: dict[str, dict],
        budget: int,
    ) -> dict:
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        sampler = TPESampler(seed=self.seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)

        def optuna_objective(trial: optuna.Trial) -> float:
            params = {}
            for key, spec in search_space.items():
                if "values" in spec:
                    params[key] = trial.suggest_categorical(key, spec["values"])
                elif "min" in spec and "max" in spec:
                    step = spec.get("step")
                    if isinstance(spec["min"], float) or isinstance(spec.get("step"), float):
                        params[key] = trial.suggest_float(key, spec["min"], spec["max"], step=step)
                    else:
                        params[key] = trial.suggest_int(key, spec["min"], spec["max"], step=step or 1)
            return objective(params)

        study.optimize(optuna_objective, n_trials=budget, show_progress_bar=False)

        history = []
        for trial in study.trials:
            entry = {"config": trial.params, "score": trial.value}
            if trial.state != optuna.trial.TrialState.COMPLETE:
                entry["error"] = str(trial.state)
            history.append(entry)

        best = study.best_trial
        return {
            "best_config": best.params,
            "best_score": best.value,
            "history": history,
            "method": "bayesian",
            "evaluated": len(study.trials),
        }
