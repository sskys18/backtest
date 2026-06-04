"""CoinAnk historical liquidation source.

Requires `COINANK_APIKEY` in env (the `coinank-apikey` browser header from
coinank.com — free, no signup; copy from the network tab). Calls
`/api/liquidation/statistic` for aggregated long/short turnover per
coin per exchange. Free, no signup, but limited to 180 buckets per call
(pagination params ignored by server as of 2026-05-02).

Coverage table (180 buckets × interval):
  5m  → 15 hours       4h  → 30 days
  15m → 45 hours       12h → 90 days
  30m → 3.75 days      1d  → 180 days
  1h  → 7.5 days

Schema mapping: response `details.{Exchange}.longTurnover/shortTurnover` (USD)
→ canonical `long_liq_usd` / `short_liq_usd`. By default sums all exchanges
('all' field). Set env `COINANK_EXCHANGE=Binance` to filter to one exchange.

Pagination/depth: server returns latest-only. To extend history beyond
180 buckets, run periodically and accumulate via `bt collect-liq`-style
daemon (TODO future work).

The embedded API key may rotate or rate-limit. If 401/403 ("system error")
starts returning, options:
  1. Back off — limits typically reset within minutes.
  2. Refresh key by opening any liquidation page on coinank.com and copying
     the `coinank-apikey` header from network tab. Override via env
     `COINANK_APIKEY=...`.
  3. For unrestricted access, sign up at coinank.com/api for a personal key.

Rate limit observed (2026-05-02): roughly tens of requests/minute per IP
before 403 cooldown. Cache aggressively if running scans repeatedly.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx
import polars as pl


_BASE = "https://api.coinank.com"
_INTERVALS = {"5m", "15m", "30m", "1h", "4h", "12h", "1d"}


def _to_interval(tf: str) -> str:
    return tf if tf in _INTERVALS else "1h"


def _base_coin(pair: str) -> str:
    return pair.split("-")[0]


class CoinankSource:
    def supported_types(self) -> list[str]:
        return ["liquidations"]

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        if data_type != "liquidations":
            raise ValueError(f"CoinankSource does not support: {data_type}")

        coin = _base_coin(pair)
        interval = _to_interval(timeframe)
        exchange = os.getenv("COINANK_EXCHANGE", "")
        key = os.getenv("COINANK_APIKEY")
        if not key:
            raise RuntimeError(
                "COINANK_APIKEY not set. Copy the `coinank-apikey` header from "
                "coinank.com (network tab) into .env as COINANK_APIKEY=..."
            )

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
            "Origin": "https://coinank.com",
            "Referer": "https://coinank.com/",
            "coinank-apikey": key,
            "client": "web",
            "web-version": "102",
            "Accept": "application/json",
        }
        params = {"baseCoin": coin, "exchangeName": "", "interval": interval}

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{_BASE}/api/liquidation/statistic", params=params, headers=headers,
            )
            r.raise_for_status()
            payload = r.json()

        rows_raw = payload.get("data", []) if payload.get("success") else []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        rows = []
        for bucket in rows_raw:
            ts = int(bucket.get("ts", 0))
            if not (start_ms <= ts <= end_ms):
                continue
            if exchange:
                src = bucket.get("details", {}).get(exchange, {})
            else:
                src = bucket.get("all", {})
            rows.append({
                "timestamp": ts,
                "long_liq_usd": float(src.get("longTurnover") or 0),
                "short_liq_usd": float(src.get("shortTurnover") or 0),
            })

        if not rows:
            return pl.DataFrame(schema={
                "timestamp": pl.Int64,
                "long_liq_usd": pl.Float64,
                "short_liq_usd": pl.Float64,
            })
        return pl.DataFrame(rows).sort("timestamp")
