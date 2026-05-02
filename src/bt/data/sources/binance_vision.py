"""Binance Vision public-data dump adapter.

Source: https://data.binance.vision/

Supports daily CSV ZIP archives for:
  - metrics: per-minute OI, top-trader LSR, taker buy/sell ratio
  - book_depth_hist: percentage-bucketed orderbook snapshots
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from zipfile import ZipFile

import httpx
import polars as pl


_BASE = "https://data.binance.vision/data/futures/um/daily"


def _symbol(pair: str) -> str:
    """Convert BTC-USDT to BTCUSDT."""
    return pair.replace("-", "")


class BinanceVisionSource:
    """Fetch Binance public-data CSV ZIPs."""

    def supported_types(self) -> list[str]:
        return ["metrics", "book_depth_hist"]

    async def fetch(
        self,
        pair: str,
        data_type: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pl.DataFrame:
        if data_type not in ("metrics", "book_depth_hist"):
            raise ValueError(f"BinanceVisionSource does not support: {data_type}")

        symbol = _symbol(pair)
        category = "metrics" if data_type == "metrics" else "bookDepth"
        frames: list[pl.DataFrame] = []

        for day in _iter_days(start.date(), end.date()):
            url = f"{_BASE}/{category}/{symbol}/{symbol}-{category}-{day.isoformat()}.zip"
            zip_bytes = await self._download_zip(url, day)
            if zip_bytes is None:
                continue
            csv = self._extract_csv(zip_bytes)
            frames.append(self._parse_metrics(csv) if data_type == "metrics" else self._parse_book_depth(csv))

        if not frames:
            return _empty_df(data_type)

        return (
            pl.concat(frames)
            .unique(subset=["timestamp"], keep="first")
            .sort("timestamp")
        )

    async def _download_zip(self, url: str, day: date) -> bytes | None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.content

    def _extract_csv(self, zip_bytes: bytes) -> str:
        with ZipFile(BytesIO(zip_bytes)) as archive:
            name = archive.namelist()[0]
            return archive.read(name).decode("utf-8")

    def _parse_metrics(self, csv: str) -> pl.DataFrame:
        df = pl.read_csv(BytesIO(csv.encode()))
        return df.select([
            pl.col("create_time")
            .str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S")
            .dt.epoch(time_unit="ms")
            .cast(pl.Int64)
            .alias("timestamp"),
            pl.col("sum_open_interest").cast(pl.Float64).alias("open_interest"),
            pl.col("sum_open_interest_value").cast(pl.Float64).alias("open_interest_value"),
            pl.col("sum_toptrader_long_short_ratio").cast(pl.Float64).alias("toptrader_lsr"),
            pl.col("count_long_short_ratio").cast(pl.Float64).alias("global_lsr"),
            pl.col("sum_taker_long_short_vol_ratio").cast(pl.Float64).alias("taker_ratio"),
        ])

    def _parse_book_depth(self, csv: str) -> pl.DataFrame:
        df = pl.read_csv(BytesIO(csv.encode()))
        return df.select([
            pl.col("timestamp").cast(pl.Int64),
            pl.col("percentage").cast(pl.Float64),
            pl.col("depth").cast(pl.Float64),
            pl.col("notional").cast(pl.Float64),
        ])


def _iter_days(start: date, end: date):
    """Yield inclusive calendar days for Binance Vision daily files."""
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _empty_df(data_type: str) -> pl.DataFrame:
    if data_type == "metrics":
        return pl.DataFrame(schema={
            "timestamp": pl.Int64,
            "open_interest": pl.Float64,
            "open_interest_value": pl.Float64,
            "toptrader_lsr": pl.Float64,
            "global_lsr": pl.Float64,
            "taker_ratio": pl.Float64,
        })
    return pl.DataFrame(schema={
        "timestamp": pl.Int64,
        "percentage": pl.Float64,
        "depth": pl.Float64,
        "notional": pl.Float64,
    })
