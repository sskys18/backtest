"""Fear & Greed Index source via alternative.me public API."""
from __future__ import annotations

from datetime import datetime

import polars as pl
from core.http.client import HttpClient


_FNG_BASE = "https://api.alternative.me"


class FngSource:
    def supported_types(self) -> list[str]:
        return ["fng"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "fng":
            raise ValueError(f"FngSource does not support: {data_type}")

        http = HttpClient(_FNG_BASE)
        try:
            resp = await http.get("/fng/", params={"limit": 0, "format": "json"})
        finally:
            await http.close()

        rows = []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        for r in resp.get("data", []):
            ts_ms = int(r["timestamp"]) * 1000
            if start_ms <= ts_ms <= end_ms:
                rows.append({
                    "timestamp": ts_ms,
                    "value": int(r["value"]),
                    "classification": str(r["value_classification"]),
                })

        if not rows:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64, "value": pl.Int64, "classification": pl.Utf8,
            })
        return pl.DataFrame(rows).sort("timestamp")
