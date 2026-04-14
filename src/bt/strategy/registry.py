"""Strategy registry — maps names to strategy classes."""

from __future__ import annotations

from bt.strategy.momentum import MomentumStrategy
from bt.strategy.mean_revert import MeanRevertStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    "momentum": MomentumStrategy,
    "mean_revert": MeanRevertStrategy,
}
