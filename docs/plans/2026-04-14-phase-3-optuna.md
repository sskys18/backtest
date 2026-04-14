# Phase 3: Bayesian Optimization with Optuna

**Spec:** `docs/specs/2026-04-13-backtest-system-design.md` section 12
**Scope:** Optuna-based Bayesian optimizer, replaces grid search for smarter exploration
**Depends on:** Phase 1 (optimizer framework + backtest pipeline)
**Testable output:** `bt optimize --method bayesian` runs Optuna TPE sampler

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `pyproject.toml` | Modify | Add `optuna` to ml dependency group |
| `src/bt/optimize/bayesian.py` | Create | Optuna TPE optimizer implementation |
| `src/bt/optimize/run.py` | Modify | Add `bayesian` method dispatch |
| `tests/python/test_optimize.py` | Create | Tests for all optimizer methods |

---

## Task 1: Add optuna dependency

File: `pyproject.toml` — update ml group:

```toml
[project.optional-dependencies]
ml = ["timesfm>=1.0", "optuna>=4.0"]
```

**Verify:** `python -c "import optuna; print(optuna.__version__)"`

---

## Task 2: Implement Bayesian optimizer

File: `src/bt/optimize/bayesian.py`

```python
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
```

**Verify:** Imports work, unit test in Task 4.

---

## Task 3: Wire into optimizer dispatch

File: `src/bt/optimize/run.py` — modify method dispatch:

```python
# Add import at top:
from bt.optimize.bayesian import BayesianOptimizer

# In run_optimize(), update the if/elif chain:
if method == "random":
    optimizer = RandomSearchOptimizer()
elif method == "bayesian":
    optimizer = BayesianOptimizer()
else:
    optimizer = GridSearchOptimizer()
```

**Verify:** `bt optimize --help` shows method option.

---

## Task 4: Tests

File: `tests/python/test_optimize.py`

```python
"""Test optimizer implementations."""

import pytest


def _mock_objective(params: dict) -> float:
    """Simple quadratic: maximize -(x-3)^2 + 10. Peak at x=3, score=10."""
    x = params.get("x", 0)
    return -(x - 3) ** 2 + 10


class TestGridSearch:
    def test_finds_best(self):
        from bt.optimize.grid import GridSearchOptimizer

        opt = GridSearchOptimizer()
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0, "max": 6, "step": 1}},
            budget=10,
        )
        assert result["best_config"]["x"] == 3
        assert result["best_score"] == pytest.approx(10.0)
        assert result["method"] == "grid"

    def test_respects_budget(self):
        from bt.optimize.grid import GridSearchOptimizer

        opt = GridSearchOptimizer()
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0, "max": 100, "step": 1}},
            budget=5,
        )
        assert result["evaluated"] <= 5


class TestRandomSearch:
    def test_returns_result(self):
        from bt.optimize.grid import RandomSearchOptimizer

        opt = RandomSearchOptimizer()
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0.0, "max": 6.0, "step": 0.1}},
            budget=20,
        )
        assert result["best_score"] is not None
        assert result["method"] == "random"
        assert result["evaluated"] == 20


class TestBayesianOptimizer:
    def test_finds_near_optimum(self):
        from bt.optimize.bayesian import BayesianOptimizer

        opt = BayesianOptimizer(seed=42)
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0.0, "max": 6.0}},
            budget=30,
        )
        # Should get close to x=3, score=10 within 30 trials
        assert result["best_score"] >= 9.0
        assert result["method"] == "bayesian"

    def test_with_integer_params(self):
        from bt.optimize.bayesian import BayesianOptimizer

        opt = BayesianOptimizer(seed=42)
        result = opt.optimize(
            _mock_objective,
            {"x": {"min": 0, "max": 6, "step": 1}},
            budget=10,
        )
        assert isinstance(result["best_config"]["x"], int)

    def test_with_categorical_params(self):
        from bt.optimize.bayesian import BayesianOptimizer

        opt = BayesianOptimizer(seed=42)
        result = opt.optimize(
            _mock_objective,
            {"x": {"values": [1, 2, 3, 4, 5]}},
            budget=10,
        )
        assert result["best_config"]["x"] in [1, 2, 3, 4, 5]
        assert result["best_config"]["x"] == 3
```

**Verify:** `uv run pytest tests/python/test_optimize.py -v` — 6 tests pass.

---

## Task 5: Commit

```bash
git add -A && git commit -m "Phase 3: Optuna Bayesian optimizer — TPE sampler, bt optimize --method bayesian"
```
