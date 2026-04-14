"""Test Gymnasium RL environment."""

import numpy as np
import polars as pl
import pytest
from pathlib import Path


@pytest.fixture
def data_file(tmp_path):
    """Create synthetic parquet data for env testing."""
    n = 100
    df = pl.DataFrame({
        "timestamp": [int(i * 3_600_000) for i in range(n)],
        "open": [100.0 + i * 0.5 for i in range(n)],
        "high": [101.0 + i * 0.5 for i in range(n)],
        "low": [99.0 + i * 0.5 for i in range(n)],
        "close": [100.0 + i * 0.5 for i in range(n)],
        "volume": [1000.0] * n,
    })
    path = tmp_path / "test_data.parquet"
    df.write_parquet(path)
    return str(path)


class TestBacktestEnv:
    def test_creation(self, data_file):
        from bt.rl.env import BacktestEnv
        env = BacktestEnv(data_path=data_file)
        assert env.action_space.shape == (2,)
        assert env.observation_space.shape == (11,)

    def test_reset(self, data_file):
        from bt.rl.env import BacktestEnv
        env = BacktestEnv(data_path=data_file)
        obs, info = env.reset()
        assert obs.shape == (11,)
        assert obs.dtype == np.float32

    def test_step(self, data_file):
        from bt.rl.env import BacktestEnv
        env = BacktestEnv(data_path=data_file)
        env.reset()
        action = np.array([0.8, 0.15], dtype=np.float32)
        obs, reward, done, truncated, info = env.step(action)
        assert obs.shape == (11,)
        assert isinstance(reward, float)
        assert isinstance(done, bool)

    def test_full_episode(self, data_file):
        from bt.rl.env import BacktestEnv
        env = BacktestEnv(data_path=data_file)
        obs, _ = env.reset()
        total_reward = 0.0
        steps = 0
        while True:
            action = env.action_space.sample()
            obs, reward, done, truncated, info = env.step(action)
            total_reward += reward
            steps += 1
            if done or truncated:
                break
        assert steps == 100  # all bars consumed
        assert (done or truncated)

    def test_gymnasium_check(self, data_file):
        """Run Gymnasium's built-in env checker."""
        from gymnasium.utils.env_checker import check_env
        from bt.rl.env import BacktestEnv
        env = BacktestEnv(data_path=data_file)
        check_env(env, skip_render_check=True)
