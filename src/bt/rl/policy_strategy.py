"""Strategy that loads a trained PPO policy and generates signals."""

from __future__ import annotations

import numpy as np
import polars as pl


class PPOStrategy:
    """Loads a trained PPO model and generates vectorized signals.

    Runs the policy forward on each bar to produce (strength, target_size).
    """

    def __init__(self, model_path: str, initial_capital: float = 10_000.0):
        self.model_path = model_path
        self.initial_capital = initial_capital

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        from stable_baselines3 import PPO

        model = PPO.load(self.model_path)

        n = len(df)
        strengths = np.zeros(n)
        target_sizes = np.zeros(n)

        # Simulate forward pass to get observations
        equity = self.initial_capital
        cash = self.initial_capital
        pos_qty = 0.0
        entry_price = 0.0
        unrealized_pnl = 0.0
        peak_equity = equity

        for i in range(n):
            row = df.row(i, named=True)
            drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0

            obs = np.array([
                row["open"], row["high"], row["low"], row["close"], row["volume"],
                equity, cash, pos_qty, entry_price, unrealized_pnl, drawdown,
            ], dtype=np.float32)

            action, _ = model.predict(obs, deterministic=True)
            strengths[i] = float(np.clip(action[0], 0.0, 1.0))
            target_sizes[i] = float(np.clip(action[1], 0.0, 1.0))

            # Approximate portfolio state update for next observation
            close = row["close"]
            if pos_qty > 0:
                unrealized_pnl = (close - entry_price) * pos_qty
                equity = cash + pos_qty * close
            else:
                equity = cash
            peak_equity = max(peak_equity, equity)

        return pl.DataFrame({
            "strength": pl.Series("strength", strengths),
            "target_size": pl.Series("target_size", target_sizes),
        })
