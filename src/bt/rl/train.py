"""PPO training using stable-baselines3."""

from __future__ import annotations

import json
from pathlib import Path

from bt.config import resolve_path


def train_ppo(
    data_path: str,
    output_dir: str = "models/ppo",
    total_timesteps: int = 100_000,
    initial_capital: float = 10_000.0,
    learning_rate: float = 3e-4,
    n_steps: int = 2048,
    batch_size: int = 64,
    n_epochs: int = 10,
    seed: int = 42,
) -> dict:
    """Train a PPO agent on historical data.

    Returns training summary with path to saved model.
    """
    from stable_baselines3 import PPO
    from stable_baselines3.common.env_util import make_vec_env

    from bt.rl.env import BacktestEnv

    resolved_data = str(resolve_path(data_path))
    output = resolve_path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    env = BacktestEnv(data_path=resolved_data, initial_capital=initial_capital)

    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=learning_rate,
        n_steps=n_steps,
        batch_size=batch_size,
        n_epochs=n_epochs,
        seed=seed,
        verbose=1,
    )

    model.learn(total_timesteps=total_timesteps)

    model_path = output / "ppo_backtest"
    model.save(str(model_path))

    # Save training config
    config = {
        "data_path": data_path,
        "total_timesteps": total_timesteps,
        "initial_capital": initial_capital,
        "learning_rate": learning_rate,
        "n_steps": n_steps,
        "batch_size": batch_size,
        "n_epochs": n_epochs,
        "seed": seed,
        "model_path": str(model_path),
    }
    (output / "train_config.json").write_text(json.dumps(config, indent=2))

    return {"status": "ok", "model_path": str(model_path), "config": config}
