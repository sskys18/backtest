"""Grid and random search optimizer."""

from __future__ import annotations

import itertools
import random
from typing import Any, Callable


class GridSearchOptimizer:
    """Exhaustive grid search over parameter space."""

    def optimize(
        self,
        objective: Callable[[dict], float],
        search_space: dict[str, dict],
        budget: int,
    ) -> dict:
        combos = _expand_grid(search_space)
        if len(combos) > budget:
            combos = combos[:budget]

        history: list[dict] = []
        best_score = float("-inf")
        best_config: dict | None = None

        for combo in combos:
            try:
                score = objective(combo)
                history.append({"config": combo, "score": score})
                if score > best_score:
                    best_score = score
                    best_config = combo
            except Exception as e:
                history.append({"config": combo, "score": None, "error": str(e)})

        return {
            "best_config": best_config,
            "best_score": best_score,
            "history": history,
            "method": "grid",
            "evaluated": len(history),
        }


class RandomSearchOptimizer:
    """Random sampling from parameter space."""

    def optimize(
        self,
        objective: Callable[[dict], float],
        search_space: dict[str, dict],
        budget: int,
    ) -> dict:
        history: list[dict] = []
        best_score = float("-inf")
        best_config: dict | None = None

        for _ in range(budget):
            combo = _sample_random(search_space)
            try:
                score = objective(combo)
                history.append({"config": combo, "score": score})
                if score > best_score:
                    best_score = score
                    best_config = combo
            except Exception as e:
                history.append({"config": combo, "score": None, "error": str(e)})

        return {
            "best_config": best_config,
            "best_score": best_score,
            "history": history,
            "method": "random",
            "evaluated": len(history),
        }


def _expand_grid(space: dict[str, dict]) -> list[dict]:
    """Expand search space into all combinations."""
    keys = list(space.keys())
    ranges = []

    for key in keys:
        spec = space[key]
        if "values" in spec:
            ranges.append(spec["values"])
        elif "min" in spec and "max" in spec:
            step = spec.get("step", 1)
            if isinstance(step, float) or isinstance(spec["min"], float):
                # Float range
                vals = []
                v = spec["min"]
                while v <= spec["max"] + 1e-10:
                    vals.append(round(v, 10))
                    v += step
                ranges.append(vals)
            else:
                ranges.append(list(range(spec["min"], spec["max"] + 1, step)))
        else:
            ranges.append([spec.get("default", 0)])

    combos = []
    for values in itertools.product(*ranges):
        combos.append(dict(zip(keys, values)))
    return combos


def _sample_random(space: dict[str, dict]) -> dict:
    """Sample a single random point from the search space."""
    combo = {}
    for key, spec in space.items():
        if "values" in spec:
            combo[key] = random.choice(spec["values"])
        elif "min" in spec and "max" in spec:
            step = spec.get("step", 1)
            if isinstance(step, float) or isinstance(spec["min"], float):
                combo[key] = round(random.uniform(spec["min"], spec["max"]), 10)
            else:
                combo[key] = random.randrange(spec["min"], spec["max"] + 1, step)
        else:
            combo[key] = spec.get("default", 0)
    return combo
