"""Scanner base types.

Each scanner declares required columns and provides:
  - compute(df, params): adds signal (0/1) and strength columns
  - entry_filter(params): optional ScanSignal replay filter
  - default_exit_rules(): TP/SL/hold defaults for replay integrations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import polars as pl

from bt.replay.loader import ScanSignal
from bt.replay.simulator import ExitRules


EntryFilter = Callable[[ScanSignal], bool]


@dataclass
class ScannerResult:
    """Per-scanner backtest output."""

    name: str
    is_approximation: bool
    required_columns: list[str]
    missing_columns: list[str]
    summary: dict = field(default_factory=dict)
    error: str | None = None


class ScannerSpec(Protocol):
    """Protocol implemented by every scanner port."""

    name: str = ""
    required_columns: list[str] = []
    is_approximation: bool = False

    def default_exit_rules(self) -> ExitRules:
        """Return default exit rules."""
        ...

    def compute(self, df: pl.DataFrame, params: dict) -> pl.DataFrame:
        """Append scanner outputs to a feature DataFrame."""
        ...

    def entry_filter(self, params: dict) -> EntryFilter | None:
        """Return a replay entry filter if ScanSignal replay is supported."""
        ...
