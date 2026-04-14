"""DataSource protocol for exchange data providers."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import polars as pl


@runtime_checkable
class DataSource(Protocol):
    """Any data provider. Supports multiple data types per source."""

    def supported_types(self) -> list[str]:
        """Returns list of supported data types."""
        ...

    async def fetch(
        self,
        pair: str,
        data_type: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        """Returns DataFrame matching the canonical schema for the given data_type."""
        ...
