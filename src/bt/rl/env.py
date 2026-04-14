"""Gymnasium environment wrapping Rust StepEngine."""

from __future__ import annotations

from pathlib import Path

import gymnasium as gym
import numpy as np
import polars as pl
from gymnasium import spaces


class BacktestEnv(gym.Env):
    """Gymnasium environment for RL-based backtesting.

    Observation: [open, high, low, close, volume, equity, cash,
                  position_qty, entry_price, unrealized_pnl, drawdown]
    Action: [strength, target_size] both continuous in [0, 1]
    Reward: log return of equity per step
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        data_path: str,
        initial_capital: float = 10_000.0,
        slippage_bps: float = 5.0,
        fee_rate: float = 0.001,
        fill_model: str = "immediate",
        max_position_size: float = 0.20,
        max_drawdown_halt: float = 0.50,
        signal_threshold: float = 0.5,
        bars_per_year: float = 8760.0,
    ):
        super().__init__()

        self.data_path = data_path
        self.initial_capital = initial_capital
        self.broker_config = {
            "slippage_bps": slippage_bps,
            "fee_rate": fee_rate,
            "fill_model": fill_model,
        }
        self.risk_config = {
            "max_position_size": max_position_size,
            "max_drawdown_halt": max_drawdown_halt,
            "signal_threshold": signal_threshold,
        }
        self.bars_per_year = bars_per_year

        # Load data
        df = pl.read_parquet(data_path)
        self._timestamps = df["timestamp"].to_numpy().astype(np.int64)
        self._opens = df["open"].to_numpy().astype(np.float64)
        self._highs = df["high"].to_numpy().astype(np.float64)
        self._lows = df["low"].to_numpy().astype(np.float64)
        self._closes = df["close"].to_numpy().astype(np.float64)
        self._volumes = df["volume"].to_numpy().astype(np.float64)

        # Action: [strength, target_size] in [0, 1]
        self.action_space = spaces.Box(low=0.0, high=1.0, shape=(2,), dtype=np.float32)

        # Observation: 11 continuous values
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(11,), dtype=np.float32,
        )

        self._engine = None

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self._create_engine()
        obs_dict = self._engine.reset()
        return self._dict_to_array(obs_dict), {}

    def step(self, action):
        strength = float(action[0])
        target_size = float(action[1])

        result = self._engine.step(strength, target_size)

        obs = self._dict_to_array(result["obs"])
        reward = float(result["reward"])
        done = bool(result["done"])
        truncated = bool(result["truncated"])

        return obs, reward, done, truncated, {}

    def _create_engine(self):
        from bt.bt_engine import StepEngine

        self._engine = StepEngine(
            initial_capital=self.initial_capital,
            broker=self.broker_config,
            risk=self.risk_config,
            bars_per_year=self.bars_per_year,
            timestamps=self._timestamps,
            opens=self._opens,
            highs=self._highs,
            lows=self._lows,
            closes=self._closes,
            volumes=self._volumes,
        )

    def _dict_to_array(self, obs_dict: dict) -> np.ndarray:
        return np.array([
            obs_dict["open"], obs_dict["high"], obs_dict["low"],
            obs_dict["close"], obs_dict["volume"],
            obs_dict["equity"], obs_dict["cash"],
            obs_dict["position_qty"], obs_dict["position_entry_price"],
            obs_dict["unrealized_pnl"], obs_dict["drawdown"],
        ], dtype=np.float32)
