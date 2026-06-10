#!/usr/bin/env python
"""Fetch Binance FUTURES order-flow microstructure → long panel for short-horizon
cross-sectional flow-signal search.

Per 5m bar, per liquid USDT-perp: price (klines), taker buy/sell ratio, and open
interest. These are the order-flow signals where short-term crypto edge plausibly
lives — unlike OHLCV price, which is arbitraged at every frequency.

Constraint: /futures/data/* (taker, OI) serve only the LAST 30 DAYS → window fixed
to 30d 5m. Universe = top-N USDT-perp by 24h quote volume (all liquid). BTC held
out as benchmark.

Writes:
  data/raw/binance_micro_panel_5m.parquet   alt panel: OHLCV + taker_ratio + oi_usd
  data/raw/binance_micro_btc_5m.parquet      BTC OHLCV benchmark
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import polars as pl

F = "https://fapi.binance.com"
OUT = Path("data/raw")
TOP_N = 50
INTERVAL = sys.argv[1] if len(sys.argv) > 1 else "5m"
LIMIT_K = 1000
LIMIT_D = 500  # futures-data max (≈500 most-recent points regardless of startTime)
# at this period, taker/OI depth ≈ 500*period: 5m→1.7d, 15m→5.2d, 1h→20.8d
_BARS_DAY = {"5m": 288, "15m": 96, "1h": 24}


def get(path: str, params: dict):
    url = F + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def universe() -> list[str]:
    info = get("/fapi/v1/exchangeInfo", {})
    perp = {s["symbol"] for s in info["symbols"]
            if s.get("contractType") == "PERPETUAL" and s["quoteAsset"] == "USDT"
            and s["status"] == "TRADING"}
    qv = {t["symbol"]: float(t["quoteVolume"]) for t in get("/fapi/v1/ticker/24hr", {})}
    ranked = sorted((s for s in perp if s in qv), key=lambda s: -qv[s])
    return ranked  # caller takes BTC + top-N alts


def paginate(path: str, symbol: str, start_ms: int, limit: int, is_data: bool) -> list:
    out, cursor = [], start_ms
    while True:
        p = {"symbol": symbol, "startTime": cursor, "limit": limit}
        if is_data:
            p["period"] = INTERVAL
        else:
            p["interval"] = INTERVAL
        rows = get(path, p)
        if not rows:
            break
        out.extend(rows)
        last_ts = rows[-1][0] if not is_data else int(rows[-1]["timestamp"])
        if len(rows) < limit:
            break
        cursor = last_ts + 1
        time.sleep(0.15)
    return out


def fetch_symbol(symbol: str, start_ms: int) -> pl.DataFrame | None:
    kl = paginate("/fapi/v1/klines", symbol, start_ms, LIMIT_K, is_data=False)
    if not kl:
        return None
    price = pl.DataFrame({
        "timestamp": [int(r[0]) for r in kl],
        "open": [float(r[1]) for r in kl], "high": [float(r[2]) for r in kl],
        "low": [float(r[3]) for r in kl], "close": [float(r[4]) for r in kl],
        "volume": [float(r[5]) for r in kl],
    })
    tk = paginate("/futures/data/takerlongshortRatio", symbol, start_ms, LIMIT_D, is_data=True)
    taker = pl.DataFrame({
        "timestamp": [int(r["timestamp"]) for r in tk],
        "taker_ratio": [float(r["buySellRatio"]) for r in tk],
    }) if tk else pl.DataFrame({"timestamp": [], "taker_ratio": []}, schema={"timestamp": pl.Int64, "taker_ratio": pl.Float64})
    oih = paginate("/futures/data/openInterestHist", symbol, start_ms, LIMIT_D, is_data=True)
    oi = pl.DataFrame({
        "timestamp": [int(r["timestamp"]) for r in oih],
        "oi_usd": [float(r["sumOpenInterestValue"]) for r in oih],
    }) if oih else pl.DataFrame({"timestamp": [], "oi_usd": []}, schema={"timestamp": pl.Int64, "oi_usd": pl.Float64})
    return price.join(taker, on="timestamp", how="left").join(oi, on="timestamp", how="left")


def main() -> None:
    # 30d window (futures-data limit). Pass an explicit start to avoid Date.now drift.
    ranked = universe()
    btc = "BTCUSDT"
    alts = [s for s in ranked if s != btc][:TOP_N]
    start_ms = None
    # derive 30d-ago from the freshest kline (robust, no clock assumption)
    probe = get("/fapi/v1/klines", {"symbol": btc, "interval": INTERVAL, "limit": 1})
    now_ms = int(probe[-1][0])
    start_ms = now_ms - 30 * 24 * 3600 * 1000
    print(f"universe top-{TOP_N} alts + BTC | 5m 30d from {start_ms}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, sym in enumerate([btc] + alts, 1):
        try:
            df = fetch_symbol(sym, start_ms)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}] {sym} FAILED: {e}", flush=True)
            continue
        if df is None:
            continue
        asset = sym.replace("USDT", "") + "-USDT"
        df = df.with_columns(pl.lit(asset).alias("asset"))
        if sym == btc:
            df.write_parquet(OUT / f"binance_micro_btc_{INTERVAL}.parquet")
            print(f"  [{i}] BTC {df.height} bars (benchmark)", flush=True)
            continue
        frames.append(df)
        if i % 10 == 0:
            cov_t = df["taker_ratio"].is_not_null().mean()
            print(f"  [{i}] {sym} {df.height} bars taker_cov={cov_t:.2f}", flush=True)

    long = pl.concat(frames).sort(["asset", "timestamp"])
    # forward-fill taker/OI gaps within each asset (5m grids can skip a bar)
    long = long.with_columns(
        pl.col("taker_ratio").forward_fill().over("asset"),
        pl.col("oi_usd").forward_fill().over("asset"),
    ).drop_nulls(["taker_ratio", "oi_usd"])
    panel = long.select(
        "timestamp", "asset", "open", "high", "low", "close", "volume",
        "taker_ratio", "oi_usd",
    ).with_columns(
        pl.lit(2, dtype=pl.UInt8).alias("session"),
        pl.lit("crypto").alias("sector"),
    )
    panel.write_parquet(OUT / f"binance_micro_panel_{INTERVAL}.parquet")
    print(f"wrote micro panel rows={panel.height} assets={panel['asset'].n_unique()}", flush=True)


if __name__ == "__main__":
    main()
