"""Strategy protocol — vectorized signal computation."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class Strategy(Protocol):
    """Strategies are vectorized. They receive the full feature DataFrame
    and return a DataFrame with signal columns. No per-bar callbacks."""

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        """Input: features DataFrame (OHLCV + indicators).
        Output: DataFrame with 'strength' (f64) and 'target_size' (f64) columns.
        Must have same row count as input. Must not look ahead."""
        ...


def create_strategy(name: str, params: dict[str, Any]) -> Strategy:
    """Create a strategy instance from the registry."""
    from bt.strategy.registry import STRATEGY_REGISTRY

    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name}. Known: {list(STRATEGY_REGISTRY.keys())}")
    return cls(**params)
