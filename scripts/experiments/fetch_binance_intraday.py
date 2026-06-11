#!/usr/bin/env python
"""Fetch Binance SPOT intraday klines → long panel for the surge-signal search.

Usage: fetch_binance_intraday.py [interval] [days] [floor_usd_24h]
  defaults: 5m, 60 days, $20,000,000 rolling-24h dollar-volume floor

Liquid-only by design: the surge signal will preferentially pick thin pumpers
where a flat fee/slippage manufactures a phantom edge. Restricting to coins whose
rolling-24h dollar volume >= floor keeps fills where a flat cost is fair.

Writes:
  data/raw/binance_spot_panel_<interval>.parquet   ALT candidates (BTC held out)
  data/raw/binance_spot_btc_<interval>.parquet      BTCUSDT, benchmark only
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl

BASE = "https://api.binance.com"
OUT_DIR = Path("data/raw")
STABLES = {
    "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "EUR", "GBP",
    "AEUR", "USD1", "XUSD", "EURI", "PAXG", "WBETH", "BFUSD",
}
_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24}


def _req(path: str, params: dict):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    r = urllib.request.urlopen(req, timeout=30)
    used = r.headers.get("X-MBX-USED-WEIGHT-1M")
    return json.load(r), (int(used) if used else 0)


def _is_junk(base: str) -> bool:
    return base in STABLES or bool(re.search(r"(UP|DOWN|BULL|BEAR)$", base))


def universe() -> list[str]:
    info, _ = _req("/api/v3/exchangeInfo", {})
    return sorted(
        s["symbol"]
        for s in info["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["status"] == "TRADING"
        and s.get("isSpotTradingAllowed")
        and not _is_junk(s["baseAsset"])
    )


def fetch_klines(symbol: str, interval: str, start_ms: int) -> list[list]:
    out: list[list] = []
    cursor = start_ms
    while True:
        kl, used = _req(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "startTime": cursor, "limit": 1000},
        )
        if not kl:
            break
        out.extend(kl)
        if len(kl) < 1000:
            break
        cursor = kl[-1][0] + 1
        if used > 1100:
            time.sleep(8)
    return out


def main() -> None:
    interval = sys.argv[1] if len(sys.argv) > 1 else "5m"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    floor = float(sys.argv[3]) if len(sys.argv) > 3 else 20_000_000.0
    bpd = _BARS_PER_DAY[interval]

    start_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
    )
    syms = universe()
    print(f"universe {len(syms)} pairs | {interval} {days}d | 24h floor ${floor:,.0f}", flush=True)

    btc_df = None
    frames: list[pl.DataFrame] = []
    for i, sym in enumerate(syms, 1):
        asset = sym.replace("USDT", "") + "-USDT"
        try:
            kl = fetch_klines(sym, interval, start_ms)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(syms)}] {sym} FAILED: {e}", flush=True)
            continue
        if not kl:
            continue
        df = pl.DataFrame({
            "timestamp": [int(r[0]) for r in kl],
            "asset": [asset] * len(kl),
            "open": [float(r[1]) for r in kl],
            "high": [float(r[2]) for r in kl],
            "low": [float(r[3]) for r in kl],
            "close": [float(r[4]) for r in kl],
            "volume": [float(r[5]) for r in kl],
            "quote_vol": [float(r[7]) for r in kl],
        })
        if sym == "BTCUSDT":
            btc_df = df
            print(f"  [{i}/{len(syms)}] BTC benchmark {df.height} bars", flush=True)
            continue
        frames.append(df)
        if i % 50 == 0:
            print(f"  [{i}/{len(syms)}] {len(frames)} alts", flush=True)

    long = pl.concat(frames).sort(["asset", "timestamp"])
    # Rolling-24h dollar volume (trailing, no look-ahead): keep liquid bars only.
    long = long.with_columns(
        pl.col("quote_vol")
        .rolling_sum(window_size=bpd, min_samples=bpd)
        .over("asset")
        .alias("dv_24h")
    )
    before = long.height
    long = long.filter(pl.col("dv_24h") >= floor)
    print(f"liquidity floor: kept {long.height}/{before} bars "
          f"({long['asset'].n_unique()} alts)", flush=True)

    panel = long.select(
        "timestamp", "asset", "open", "high", "low", "close", "volume"
    ).with_columns(
        pl.lit(2, dtype=pl.UInt8).alias("session"),
        pl.lit("crypto").alias("sector"),
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pp = OUT_DIR / f"binance_spot_panel_{interval}.parquet"
    panel.write_parquet(pp)
    print(f"wrote {pp} rows={panel.height} assets={panel['asset'].n_unique()}", flush=True)
    if btc_df is not None:
        bp = OUT_DIR / f"binance_spot_btc_{interval}.parquet"
        btc_df.write_parquet(bp)
        print(f"wrote {bp} rows={btc_df.height}", flush=True)


if __name__ == "__main__":
    main()
