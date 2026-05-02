"""Coinglass v3 liquidation history adapter.

Requires `COINGLASS_API_KEY` from the Coinglass free tier.
"""

from __future__ import annotations

import os
from datetime import datetime

import httpx
import polars as pl


_BASE = "https://open-api-v3.coinglass.com/api/futures/liquidation"


class CoinglassSource:
    """Fetch historical liquidations from Coinglass."""

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
            raise ValueError(f"CoinglassSource does not support: {data_type}")

        key = os.getenv("COINGLASS_API_KEY")
        if not key:
            raise RuntimeError(
                "COINGLASS_API_KEY env var required. Free tier: https://www.coinglass.com/api"
            )

        payload = await self._get(key, pair.split("-")[0], _to_interval(timeframe))
        data = payload.get("data", []) if isinstance(payload, dict) else []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        rows = []
        for point in data:
            ts = int(point.get("t") or point.get("time") or point.get("timestamp") or 0)
            if start_ms <= ts <= end_ms:
                rows.append({
                    "timestamp": ts,
                    "long_liq_usd": float(point.get("longLiquidationUsd") or point.get("l") or 0.0),
                    "short_liq_usd": float(point.get("shortLiquidationUsd") or point.get("s") or 0.0),
                })

        return pl.DataFrame(rows).sort("timestamp") if rows else _empty_liq()

    async def _get(self, key: str, symbol: str, interval: str):
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{_BASE}/history",
                params={"symbol": symbol, "interval": interval},
                headers={"CG-API-KEY": key, "accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()


def _to_interval(timeframe: str) -> str:
    mapping = {
        "1m": "m1",
        "5m": "m5",
        "15m": "m15",
        "30m": "m30",
        "1h": "h1",
        "4h": "h4",
        "1d": "d1",
    }
    return mapping.get(timeframe, "h1")


def _empty_liq() -> pl.DataFrame:
    return pl.DataFrame(schema={
        "timestamp": pl.Int64,
        "long_liq_usd": pl.Float64,
        "short_liq_usd": pl.Float64,
    })
