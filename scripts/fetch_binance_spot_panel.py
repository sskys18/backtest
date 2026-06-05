#!/usr/bin/env python
"""Fetch Binance SPOT USDT klines → long panel parquet for the alt-vs-BTC test.

One-off experiment fetcher (repo is import-only for the *core* package; this is
a throwaway script under scripts/, not wired into bt.data). Pulls daily klines
for every TRADING USDT spot pair, filters stablecoins + leveraged tokens, applies
a trailing dollar-volume liquidity floor, and writes:

  data/raw/binance_spot_panel_1d.parquet   long panel of ALT candidates (BTC held out)
  data/raw/binance_spot_btc_1d.parquet     BTCUSDT series, benchmark only

Survivorship WARNING (baked into the experiment's interpretation): the public
klines API only serves *currently listed* symbols. Every delisted coin is absent
→ directionally anti-momentum (the basket's worst trades are deleted). So a basket
that LOSES to BTC despite this tailwind is a trustworthy negative; a basket that
BEATS BTC is confounded, not alpha.
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
INTERVAL = "1d"
MONTHS_BACK = 18
LIQ_FLOOR_USD = 1_000_000.0  # trailing 7-bar median quote-volume floor
LIQ_WINDOW = 7
OUT_DIR = Path("data/raw")

STABLES = {
    "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "EUR", "GBP",
    "AEUR", "USD1", "XUSD", "EURI", "PAXG", "WBETH", "BFUSD",
}


def _req(path: str, params: dict):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    r = urllib.request.urlopen(req, timeout=30)
    used = r.headers.get("X-MBX-USED-WEIGHT-1M")
    return json.load(r), (int(used) if used else 0)


def _is_junk(base: str) -> bool:
    if base in STABLES:
        return True
    if re.search(r"(UP|DOWN|BULL|BEAR)$", base):  # leveraged tokens
        return True
    return False


def universe() -> list[str]:
    info, _ = _req("/api/v3/exchangeInfo", {})
    syms = [
        s["symbol"]
        for s in info["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["status"] == "TRADING"
        and s.get("isSpotTradingAllowed")
        and not _is_junk(s["baseAsset"])
    ]
    return sorted(syms)


def fetch_klines(symbol: str, start_ms: int) -> list[list]:
    """Daily klines from start_ms to now. 18mo < 1000 bars → single page,
    but paginate defensively."""
    out: list[list] = []
    cursor = start_ms
    while True:
        kl, used = _req(
            "/api/v3/klines",
            {"symbol": symbol, "interval": INTERVAL, "startTime": cursor, "limit": 1000},
        )
        if not kl:
            break
        out.extend(kl)
        if len(kl) < 1000:
            break
        cursor = kl[-1][0] + 1
        if used > 1000:  # throttle on real used-weight, not a guess
            time.sleep(10)
    return out


def klines_to_df(symbol: str, kl: list[list], asset: str) -> pl.DataFrame:
    # kline: [openTime, o, h, l, c, vol, closeTime, quoteVol, nTrades, ...]
    return pl.DataFrame(
        {
            "timestamp": [int(r[0]) for r in kl],
            "asset": [asset] * len(kl),
            "open": [float(r[1]) for r in kl],
            "high": [float(r[2]) for r in kl],
            "low": [float(r[3]) for r in kl],
            "close": [float(r[4]) for r in kl],
            "volume": [float(r[5]) for r in kl],
            "quote_vol": [float(r[7]) for r in kl],
        }
    )


def main() -> None:
    start_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=MONTHS_BACK * 30)).timestamp() * 1000
    )
    syms = universe()
    print(f"universe: {len(syms)} USDT spot pairs (incl BTCUSDT held out)", flush=True)

    btc_df = None
    frames: list[pl.DataFrame] = []
    for i, sym in enumerate(syms, 1):
        asset = sym.replace("USDT", "") + "-USDT"
        try:
            kl = fetch_klines(sym, start_ms)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(syms)}] {sym} FAILED: {e}", flush=True)
            continue
        if not kl:
            continue
        df = klines_to_df(sym, kl, asset)
        if sym == "BTCUSDT":
            btc_df = df
            print(f"  [{i}/{len(syms)}] {sym} -> BTC benchmark ({df.height} bars)", flush=True)
            continue
        frames.append(df)
        if i % 50 == 0:
            print(f"  [{i}/{len(syms)}] fetched, {len(frames)} alts so far", flush=True)

    long = pl.concat(frames)

    # Trailing liquidity floor: per-asset 7-bar median quote-volume >= floor.
    # Trailing (closed window) → no look-ahead. Sub-floor bars dropped → those
    # (t,asset) cells become NaN in the wide panel → active_mask=False → excluded.
    long = long.sort(["asset", "timestamp"]).with_columns(
        pl.col("quote_vol")
        .rolling_median(window_size=LIQ_WINDOW, min_periods=LIQ_WINDOW)
        .over("asset")
        .alias("liq_med")
    )
    before = long.height
    long = long.filter(pl.col("liq_med") >= LIQ_FLOOR_USD)
    print(
        f"liquidity floor ${LIQ_FLOOR_USD:,.0f}: kept {long.height}/{before} alt-bars "
        f"({long['asset'].n_unique()} alts survive)",
        flush=True,
    )

    # Panel schema: spot daily → session=2 (trading), sector=crypto, no funding.
    panel = long.select(
        "timestamp", "asset", "open", "high", "low", "close", "volume"
    ).with_columns(
        pl.lit(2, dtype=pl.UInt8).alias("session"),
        pl.lit("crypto").alias("sector"),
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    panel_path = OUT_DIR / "binance_spot_panel_1d.parquet"
    panel.write_parquet(panel_path)
    print(f"wrote {panel_path}  rows={panel.height} assets={panel['asset'].n_unique()}", flush=True)

    if btc_df is not None:
        btc_path = OUT_DIR / "binance_spot_btc_1d.parquet"
        btc_df.write_parquet(btc_path)
        print(f"wrote {btc_path}  rows={btc_df.height}", flush=True)
    else:
        print("WARNING: BTCUSDT not fetched — benchmark missing", file=sys.stderr)


if __name__ == "__main__":
    main()
