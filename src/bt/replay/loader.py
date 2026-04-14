"""Load and parse trades.jsonl scan data."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import polars as pl


@dataclass
class ScanSignal:
    """A single coin observation from a scan event."""
    timestamp_ms: int
    market: str
    price: float
    score: float
    change_24h: float
    volume_ratio: float
    bid_ratio: float
    rsi_15m: float
    ema_cross: float


@dataclass
class ScanLog:
    """All scan signals + OHLCV data loaded and ready for replay."""
    signals: list[ScanSignal]
    ohlcv: dict[str, pl.DataFrame]  # market → 1min OHLCV DataFrame


def load_scan_log(
    jsonl_path: str | Path,
    ohlcv_dir: str | Path,
) -> ScanLog:
    """Load trades.jsonl scan events and pair with OHLCV data.

    Args:
        jsonl_path: Path to trades.jsonl
        ohlcv_dir: Directory containing data/raw/upbit/{pair}/ohlcv/1m/data.parquet
    """
    jsonl_path = Path(jsonl_path)
    ohlcv_dir = Path(ohlcv_dir)

    # Parse scan signals
    signals = []
    with open(jsonl_path) as f:
        for line in f:
            d = json.loads(line)
            if d['event'] != 'scan':
                continue
            ts = int(datetime.fromisoformat(d['ts']).timestamp() * 1000)
            for sig in d.get('top_signals', []):
                signals.append(ScanSignal(
                    timestamp_ms=ts,
                    market=sig['market'],
                    price=float(sig.get('price', 0)),
                    score=float(sig.get('score', 0)),
                    change_24h=float(sig.get('change_24h', 0)),
                    volume_ratio=float(sig.get('volume_ratio', 0)),
                    bid_ratio=float(sig.get('bid_ratio', 0)),
                    rsi_15m=float(sig.get('rsi_15m', 0)),
                    ema_cross=float(sig.get('ema_cross', 0)),
                ))

    # Load OHLCV data for each market
    ohlcv = {}
    markets = {s.market for s in signals}
    for market in markets:
        pair = market.replace("KRW-", "") + "-KRW"
        pq_path = ohlcv_dir / "upbit" / pair / "ohlcv" / "1m" / "data.parquet"
        if pq_path.exists():
            ohlcv[market] = pl.read_parquet(pq_path)

    return ScanLog(signals=signals, ohlcv=ohlcv)
