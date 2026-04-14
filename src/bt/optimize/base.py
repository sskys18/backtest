"""Optimizer protocol."""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Optimizer(Protocol):
    """Runs multiple backtests with different configs."""

    def optimize(
        self,
        objective: Callable[[dict], float],
        search_space: dict,
        budget: int,
    ) -> dict:
        """Returns {best_config, best_score, history: [{config, score}]}"""
        ...
