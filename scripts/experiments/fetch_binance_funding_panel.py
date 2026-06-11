#!/usr/bin/env python
"""Fetch Binance USDT-perp funding history + daily klines → long panels.

One-off experiment fetcher (scripts/, not wired into bt.data). Unlike the
taker/OI endpoints (hard 500-point cap), /fapi/v1/fundingRate paginates to
FULL history (verified: BTCUSDT returns 2020-01-01 data). Daily klines also
paginate fully.

Outputs:
  data/raw/binance_funding_panel.parquet   (timestamp, asset, funding_rate)
  data/raw/binance_perp_panel_1d.parquet   (timestamp, asset, ohlcv + quote_vol)

Survivorship WARNING: fapi exchangeInfo lists only live contracts. Delisted
perps (often the most extreme funding) are absent. Direction for a funding
strategy is ambiguous (extreme-negative-funding coins that died are missing
from the SHORT-able set too) — state it in the writeup, don't ignore it.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import polars as pl

BASE = "https://fapi.binance.com"
OUT_DIR = Path("data/raw")
STABLES = {"USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP", "AEUR", "USD1", "EURI"}


def _req(path: str, params: dict):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    r = urllib.request.urlopen(req, timeout=30)
    used = r.headers.get("X-MBX-USED-WEIGHT-1M")
    return json.load(r), (int(used) if used else 0)


def universe() -> list[str]:
    info, _ = _req("/fapi/v1/exchangeInfo", {})
    syms = [
        s["symbol"]
        for s in info["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["contractType"] == "PERPETUAL"
        and s["status"] == "TRADING"
        and s["baseAsset"] not in STABLES
    ]
    return sorted(syms)


def fetch_funding(symbol: str) -> list[dict]:
    out: list[dict] = []
    cursor = 1546300800000  # 2019-01-01, before any USDT perp listed
    while True:
        rows, used = _req(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": cursor, "limit": 1000},
        )
        if not rows:
            break
        out.extend(rows)
        if len(rows) < 1000:
            break
        cursor = rows[-1]["fundingTime"] + 1
        if used > 1500:
            time.sleep(10)
    return out


def fetch_klines_1d(symbol: str) -> list[list]:
    out: list[list] = []
    cursor = 1546300800000
    while True:
        kl, used = _req(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": "1d", "startTime": cursor, "limit": 1000},
        )
        if not kl:
            break
        out.extend(kl)
        if len(kl) < 1000:
            break
        cursor = kl[-1][0] + 1
        if used > 1500:
            time.sleep(10)
    return out


def main() -> None:
    syms = universe()
    print(f"universe: {len(syms)} USDT perpetuals", flush=True)

    f_frames: list[pl.DataFrame] = []
    k_frames: list[pl.DataFrame] = []
    for i, sym in enumerate(syms, 1):
        asset = sym.removesuffix("USDT") + "-USDT"
        try:
            fr = fetch_funding(sym)
            kl = fetch_klines_1d(sym)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(syms)}] {sym} FAILED: {e}", flush=True)
            continue
        if fr:
            f_frames.append(
                pl.DataFrame(
                    {
                        "timestamp": [int(r["fundingTime"]) for r in fr],
                        "asset": [asset] * len(fr),
                        "funding_rate": [float(r["fundingRate"]) for r in fr],
                    }
                )
            )
        if kl:
            k_frames.append(
                pl.DataFrame(
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
            )
        if i % 50 == 0:
            print(f"  [{i}/{len(syms)}] fetched", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    funding = pl.concat(f_frames).sort(["asset", "timestamp"])
    fp = OUT_DIR / "binance_funding_panel.parquet"
    funding.write_parquet(fp)
    print(
        f"wrote {fp}  rows={funding.height} assets={funding['asset'].n_unique()} "
        f"range={funding['timestamp'].min()}..{funding['timestamp'].max()}",
        flush=True,
    )

    perp = pl.concat(k_frames).sort(["asset", "timestamp"])
    kp = OUT_DIR / "binance_perp_panel_1d.parquet"
    perp.write_parquet(kp)
    print(
        f"wrote {kp}  rows={perp.height} assets={perp['asset'].n_unique()}",
        flush=True,
    )

    if not f_frames or not k_frames:
        print("WARNING: empty fetch", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
