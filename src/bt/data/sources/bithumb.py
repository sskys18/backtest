"""Bithumb data source — OHLCV for Korean exchange cross-reference."""

from __future__ import annotations

from datetime import datetime

import polars as pl
from core.http.client import HttpClient
from exchanges.bithumb.spot.rest_client import BithumbSpotRestClient
from exchanges.bithumb.spot.constants import BASE_URL


def _parse_pair(pair: str) -> tuple[str, str]:
    """Convert BTC-KRW → (BTC, KRW)."""
    parts = pair.split("-")
    return parts[0], parts[1] if len(parts) > 1 else "KRW"


def _timeframe_to_interval(tf: str) -> str:
    """Convert bt timeframe to Bithumb chart_intervals."""
    mapping = {
        "1m": "1m", "3m": "3m", "5m": "5m", "10m": "10m",
        "30m": "30m", "1h": "1h", "4h": "6h", "1d": "24h",
    }
    return mapping.get(tf, "1h")


class BithumbSource:
    """Bithumb Korean exchange — OHLCV data."""

    def supported_types(self) -> list[str]:
        return ["ohlcv"]

    async def fetch(
        self,
        pair: str,
        data_type: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        order_currency, payment_currency = _parse_pair(pair)
        interval = _timeframe_to_interval(timeframe)

        http = HttpClient(BASE_URL)
        client = BithumbSpotRestClient(http)

        try:
            result = await client.get_candlestick(
                order_currency=order_currency,
                payment_currency=payment_currency,
                chart_intervals=interval,
            )

            # Bithumb returns candlestick data as list of [timestamp, open, close, high, low, volume]
            rows = []
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

            data = result.data if hasattr(result, 'data') else result
            if isinstance(data, list):
                for candle in data:
                    ts = int(candle[0])
                    if start_ms <= ts <= end_ms:
                        rows.append({
                            "timestamp": ts,
                            "open": float(candle[1]),
                            "high": float(candle[3]),
                            "low": float(candle[4]),
                            "close": float(candle[2]),
                            "volume": float(candle[5]),
                        })
        finally:
            await http.close()

        if not rows:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "open": pl.Float64, "high": pl.Float64,
                "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
            })

        return pl.DataFrame(rows).sort("timestamp")
