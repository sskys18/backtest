"""YAML config loader + Pydantic validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class StrategyConfig(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class BacktestConfig(BaseModel):
    pair: str = "BTC-USDT"
    timeframe: str = "1h"
    start: str | None = None
    end: str | None = None
    initial_capital: float = 10_000.0
    data_path: str = "data/features/BTC-USDT-1h.parquet"

    @property
    def bars_per_year(self) -> float:
        mapping = {
            "1m": 525_600,
            "5m": 105_120,
            "15m": 35_040,
            "1h": 8_760,
            "4h": 2_190,
            "1d": 365,
        }
        return float(mapping.get(self.timeframe, 8_760))


class BrokerConfig(BaseModel):
    slippage_bps: float = 5.0
    fee_rate: float = 0.001
    fill_model: str = "next_bar"

    @field_validator("fill_model")
    @classmethod
    def validate_fill_model(cls, v: str) -> str:
        if v not in ("immediate", "next_bar"):
            raise ValueError(f"fill_model must be 'immediate' or 'next_bar', got '{v}'")
        return v


class RiskConfig(BaseModel):
    max_position_size: float = 0.20
    max_drawdown_halt: float = 0.15
    signal_threshold: float = 0.5


class Config(BaseModel):
    strategy: StrategyConfig
    backtest: BacktestConfig = BacktestConfig()
    broker: BrokerConfig = BrokerConfig()
    risk: RiskConfig = RiskConfig()


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config file. Paths resolved relative to project root."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Config.model_validate(raw)


def find_project_root() -> Path:
    """Walk up from cwd to find pyproject.toml."""
    p = Path.cwd()
    while p != p.parent:
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()


def resolve_path(config_path: str) -> Path:
    """Resolve a config-relative path against the project root."""
    root = find_project_root()
    return root / config_path
