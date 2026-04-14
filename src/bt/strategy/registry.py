"""Strategy registry — maps names to strategy classes."""

from __future__ import annotations

from bt.strategy.momentum import MomentumStrategy
from bt.strategy.mean_revert import MeanRevertStrategy
from bt.strategy.timesfm_signal import TimesFMStrategy
from bt.rl.policy_strategy import PPOStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    "momentum": MomentumStrategy,
    "mean_revert": MeanRevertStrategy,
    "timesfm_signal": TimesFMStrategy,
    "ppo": PPOStrategy,
}
