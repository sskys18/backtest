"""Coinalyze liquidation history adapter.

Requires `COINALYZE_API_KEY` from the Coinalyze free tier.
"""

from __future__ import annotations

import os
from datetime import datetime

import httpx
import polars as pl


_BASE = "https://api.coinalyze.net/v1"


class CoinalyzeSource:
    """Fetch historical liquidations from Coinalyze."""

    def supported_types(self) -> list[str]:
        return ["liquidations"]

    async def fetch(
        self,
        pair: str,
        data_type: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        if data_type != "liquidations":
            raise ValueError(f"CoinalyzeSource does not support: {data_type}")

        key = os.getenv("COINALYZE_API_KEY")
        if not key:
            raise RuntimeError(
                "COINALYZE_API_KEY env var required. Free tier: https://coinalyze.net/api-docs/"
            )

        symbol = pair.replace("-", "") + "_PERP.A"
        response = await self._get(
            key=key,
            symbol=symbol,
            interval=_to_interval(timeframe),
            from_s=int(start.timestamp()),
            to_s=int(end.timestamp()),
        )
        if not response:
            return _empty_liq()

        history = response[0].get("history", []) if isinstance(response, list) else []
        rows = [{
            "timestamp": int(point["t"]) * 1000,
            "long_liq_usd": float(point.get("l", 0.0)),
            "short_liq_usd": float(point.get("s", 0.0)),
        } for point in history]

        return pl.DataFrame(rows).sort("timestamp") if rows else _empty_liq()

    async def _get(self, key: str, symbol: str, interval: str, from_s: int, to_s: int):
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{_BASE}/liquidation-history",
                params={
                    "symbols": symbol,
                    "interval": interval,
                    "from": from_s,
                    "to": to_s,
                    "convert_to_usd": "true",
                },
                headers={"api_key": key},
            )
            response.raise_for_status()
            return response.json()


def _to_interval(timeframe: str) -> str:
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1hour",
        "4h": "4hour",
        "1d": "daily",
    }
    return mapping.get(timeframe, "1hour")


def _empty_liq() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "timestamp": pl.Int64,
        "long_liq_usd": pl.Float64,
        "short_liq_usd": pl.Float64,
    })
