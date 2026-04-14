"""Upbit data source — OHLCV for Korean exchange cross-reference."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import polars as pl
from core.http.client import HttpClient
from exchanges.upbit.spot.rest_client import UpbitSpotRestClient
from exchanges.upbit.spot.constants import BASE_URL


def _to_market(pair: str) -> str:
    """Convert BTC-KRW → KRW-BTC (Upbit format: quote-base)."""
    parts = pair.split("-")
    if len(parts) == 2:
        return f"{parts[1]}-{parts[0]}"
    return pair


def _timeframe_to_unit(tf: str) -> int:
    """Convert bt timeframe to Upbit candle unit (minutes)."""
    mapping = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
    return mapping.get(tf, 60)


class UpbitSource:
    """Upbit Korean exchange — OHLCV data with pagination."""

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
        market = _to_market(pair)
        unit = _timeframe_to_unit(timeframe)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        http = HttpClient(BASE_URL)
        client = UpbitSpotRestClient(http)

        all_rows = []
        # Upbit paginates backwards from `to` datetime
        current_to = end.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            while True:
                if timeframe == "1d":
                    candles = await client.get_candles_days(
                        market=market, to=current_to, count=200,
                    )
                else:
                    candles = await client.get_candles_minutes(
                        unit=unit, market=market, to=current_to, count=200,
                    )

                if not candles:
                    break

                for c in candles:
                    ts = c.timestamp if hasattr(c, 'timestamp') else int(c.get("timestamp", 0))
                    if ts < start_ms:
                        continue
                    if ts > end_ms:
                        continue

                    if hasattr(c, 'opening_price'):
                        all_rows.append({
                            "timestamp": ts,
                            "open": float(c.opening_price),
                            "high": float(c.high_price),
                            "low": float(c.low_price),
                            "close": float(c.trade_price),
                            "volume": float(c.candle_acc_trade_volume),
                        })
                    else:
                        all_rows.append({
                            "timestamp": ts,
                            "open": float(c.get("opening_price", 0)),
                            "high": float(c.get("high_price", 0)),
                            "low": float(c.get("low_price", 0)),
                            "close": float(c.get("trade_price", 0)),
                            "volume": float(c.get("candle_acc_trade_volume", 0)),
                        })

                # Check if we've gone past start
                earliest = candles[-1]
                earliest_ts = earliest.timestamp if hasattr(earliest, 'timestamp') else int(earliest.get("timestamp", 0))
                if earliest_ts <= start_ms:
                    break

                # Move the window back
                earliest_dt = earliest.candle_date_time_utc if hasattr(earliest, 'candle_date_time_utc') else earliest.get("candle_date_time_utc", "")
                if earliest_dt:
                    current_to = earliest_dt
                else:
                    break

                # Rate limit
                await asyncio.sleep(0.1)

        finally:
            await http.close()

        if not all_rows:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "open": pl.Float64, "high": pl.Float64,
                "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
            })

        return pl.DataFrame(all_rows).sort("timestamp").unique(subset=["timestamp"])
