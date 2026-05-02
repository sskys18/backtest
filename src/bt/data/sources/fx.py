"""FX rate source.

Current-day rates come from ExchangeRate-API. Historical USD/KRW uses Upbit
USDT-KRW spot OHLCV close as a free USD proxy.
"""
from __future__ import annotations

from datetime import datetime

import polars as pl
from core.http.client import HttpClient

from bt.data.sources.upbit import UpbitSource


_FX_BASE = "https://api.exchangerate-api.com"


class FxSource:
    def supported_types(self) -> list[str]:
        return ["fx"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "fx":
            raise ValueError(f"FxSource does not support: {data_type}")

        base, quote = pair.split("-")
        is_same_day = start.date() == end.date()

        if is_same_day and base == "USD":
            return await self._fetch_current(pair, quote)
        return await self._fetch_historical(pair, base, quote, timeframe, start, end)

    async def _fetch_current(self, pair: str, quote: str) -> pl.DataFrame:
        http = HttpClient(_FX_BASE)
        try:
            resp = await http.get("/v4/latest/USD")
        finally:
            await http.close()
        rate = float(resp["rates"][quote])
        ts = int(resp.get("time_last_updated", 0)) * 1000
        return pl.DataFrame([{"timestamp": ts, "pair": pair, "rate": rate}])

    async def _fetch_historical(
        self, pair: str, base: str, quote: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if not (base == "USD" and quote == "KRW"):
            raise ValueError(
                f"Historical FX only supports USD-KRW (proxy via Upbit USDT-KRW); got {pair}"
            )
        upbit = UpbitSource()
        df = await upbit.fetch(
            pair="USDT-KRW", data_type="ohlcv",
            timeframe=timeframe, start=start, end=end,
        )
        if len(df) == 0:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "pair": pl.Utf8, "rate": pl.Float64,
            })
        return df.select([
            pl.col("timestamp"),
            pl.lit(pair).alias("pair"),
            pl.col("close").alias("rate"),
        ])
