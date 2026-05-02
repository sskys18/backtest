"""Binance Futures liquidation stream collector.

Live-only. Binance removed historical liquidations REST in 2021.
Streams !forceOrder@arr WS, aggregates to 1-minute buckets, and appends daily
Parquet partitions.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import websockets

WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
BUCKET_MS = 60_000


def aggregate_messages(msgs: list[dict]) -> pl.DataFrame:
    """Aggregate raw forceOrder messages to 1-minute (symbol, timestamp) buckets."""
    rows = []
    for msg in msgs:
        order = msg.get("o", {})
        ts = int(order.get("T") or msg.get("E", 0))
        bucket = (ts // BUCKET_MS) * BUCKET_MS
        symbol = str(order.get("s", ""))
        side = str(order.get("S", ""))
        qty = float(order.get("q", 0))
        price = float(order.get("ap") or order.get("p", 0))
        usd = qty * price
        rows.append({
            "timestamp": bucket,
            "symbol": symbol,
            "long_liq_usd": usd if side == "SELL" else 0.0,
            "short_liq_usd": usd if side == "BUY" else 0.0,
        })

    if not rows:
        return pl.DataFrame(schema={
            "timestamp": pl.Int64,
            "symbol": pl.Utf8,
            "long_liq_usd": pl.Float64,
            "short_liq_usd": pl.Float64,
        })

    return (
        pl.DataFrame(rows)
        .group_by(["timestamp", "symbol"])
        .agg([
            pl.col("long_liq_usd").sum(),
            pl.col("short_liq_usd").sum(),
        ])
        .sort(["timestamp", "symbol"])
    )


def partition_path(root: Path, symbol: str, ts_ms: int) -> Path:
    """Return data/raw/binance/{pair}/liquidations/1m/YYYY-MM-DD.parquet."""
    pair = symbol.replace("USDT", "-USDT") if "USDT" in symbol and "-" not in symbol else symbol
    day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return root / "data" / "raw" / "binance" / pair / "liquidations" / "1m" / f"{day}.parquet"


async def _writer_loop(buffer: list[dict], root: Path, flush_interval: float = 60.0) -> None:
    """Periodically drain buffered messages to daily parquet files."""
    while True:
        await asyncio.sleep(flush_interval)
        if not buffer:
            continue

        msgs = buffer.copy()
        buffer.clear()
        df = aggregate_messages(msgs)

        for symbol in df["symbol"].unique().to_list():
            symbol_df = df.filter(pl.col("symbol") == symbol).drop("symbol")
            for ts in symbol_df["timestamp"].to_list():
                path = partition_path(root, symbol, ts)
                path.parent.mkdir(parents=True, exist_ok=True)
                row_df = symbol_df.filter(pl.col("timestamp") == ts)
                if path.exists():
                    existing = pl.read_parquet(path)
                    merged = (
                        pl.concat([existing, row_df])
                        .group_by("timestamp")
                        .agg([
                            pl.col("long_liq_usd").sum(),
                            pl.col("short_liq_usd").sum(),
                        ])
                        .sort("timestamp")
                    )
                    merged.write_parquet(path)
                else:
                    row_df.write_parquet(path)


async def collect(root: Path, flush_interval: float = 60.0) -> None:
    """Connect to Binance forceOrder stream, buffer messages, and reconnect on drops."""
    buffer: list[dict] = []
    writer = asyncio.create_task(_writer_loop(buffer, root, flush_interval))
    try:
        while True:
            try:
                async with websockets.connect(WS_URL) as ws:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("e") == "forceOrder":
                            buffer.append(msg)
            except (websockets.ConnectionClosed, OSError):
                await asyncio.sleep(2.0)
    finally:
        writer.cancel()
