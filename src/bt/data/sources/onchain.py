"""On-chain BTC source via blockchain.info charts API."""
from __future__ import annotations

import asyncio
from datetime import datetime

import polars as pl
from core.http.client import HttpClient


_ONCHAIN_BASE = "https://api.blockchain.info"
_CHARTS = ("n-transactions", "hash-rate", "transaction-fees-usd")
_COL_MAP = {
    "n-transactions": "tx_count",
    "hash-rate": "hashrate",
    "transaction-fees-usd": "fees_usd",
}
_DAY_MS = 86_400_000


class OnchainSource:
    def supported_types(self) -> list[str]:
        return ["onchain"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "onchain":
            raise ValueError(f"OnchainSource does not support: {data_type}")

        days = max(1, int((end - start).total_seconds() // 86_400) + 1)
        timespan = f"{days}days"

        http = HttpClient(_ONCHAIN_BASE)
        try:
            results = await asyncio.gather(*[
                http.get(f"/charts/{c}", params={
                    "timespan": timespan, "format": "json", "sampled": "false",
                })
                for c in _CHARTS
            ])
        finally:
            await http.close()

        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        frames = []
        for chart, resp in zip(_CHARTS, results):
            col = _COL_MAP[chart]
            rows = []
            for v in resp.get("values", []):
                ts_ms = int(v["x"]) * 1000
                if start_ms <= ts_ms <= end_ms:
                    bucket = (ts_ms // _DAY_MS) * _DAY_MS
                    rows.append({"timestamp": bucket, col: float(v["y"])})
            if rows:
                frame = pl.DataFrame(rows).group_by("timestamp").agg(
                    pl.col(col).mean()
                )
                frames.append(frame)

        if not frames:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "tx_count": pl.Int64,
                "hashrate": pl.Float64, "fees_usd": pl.Float64,
            })

        df = frames[0]
        for frame in frames[1:]:
            df = df.join(frame, on="timestamp", how="full", coalesce=True)
        if "tx_count" in df.columns:
            df = df.with_columns(pl.col("tx_count").cast(pl.Int64))
        return df.sort("timestamp")
