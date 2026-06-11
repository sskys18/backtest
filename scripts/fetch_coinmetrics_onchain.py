#!/usr/bin/env python
"""Fetch CoinMetrics community (free, keyless) daily on-chain metrics for the
perp-universe overlap → long panel parquet.

Community tier serves a 31-metric subset; the useful per-coin ones with
cross-sectional coverage (33-34 assets overlap Binance USDT perps):
  AdrActCnt, TxCnt, TxTfrCnt, CapMVRVCur, CapMrktCurUSD, FeeTotNtv

Output: data/raw/coinmetrics_onchain_1d.parquet
  (date, asset [BASE-USDT], adr_act, tx_cnt, tx_tfr, mvrv, mcap_usd, fee_ntv)

Coverage is majors-only (CM community drops most small caps) → the test is on
LIQUID majors; small-cap on-chain would need paid Glassnode/CM pro.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import polars as pl

BASE = "https://community-api.coinmetrics.io/v4"
OUT = Path("data/raw/coinmetrics_onchain_1d.parquet")
METRICS = ["AdrActCnt", "TxCnt", "TxTfrCnt", "CapMVRVCur", "CapMrktCurUSD", "FeeTotNtv"]
COL = {
    "AdrActCnt": "adr_act",
    "TxCnt": "tx_cnt",
    "TxTfrCnt": "tx_tfr",
    "CapMVRVCur": "mvrv",
    "CapMrktCurUSD": "mcap_usd",
    "FeeTotNtv": "fee_ntv",
}


def _req(path: str, params: dict) -> dict:
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def overlap_assets() -> dict[str, str]:
    """cm asset id -> panel asset name, for perp-universe overlap."""
    cat = _req("/catalog-v2/asset-metrics", {"page_size": 2000})
    perp = pl.read_parquet("data/raw/binance_perp_panel_1d.parquet")["asset"].unique()
    bases: dict[str, str] = {}
    for p in perp:
        b = p.removesuffix("-USDT").lower()
        bases[b] = p
        if b.startswith("1000"):
            bases[b[4:]] = p
    out = {}
    for a in cat["data"]:
        mets = {m["metric"] for m in a["metrics"]}
        if a["asset"] in bases and ("CapMVRVCur" in mets or "AdrActCnt" in mets):
            out[a["asset"]] = bases[a["asset"]]
    return out


def fetch_asset(cm_id: str) -> pl.DataFrame | None:
    rows: list[dict] = []
    params = {
        "assets": cm_id,
        "metrics": ",".join(METRICS),
        "frequency": "1d",
        "start_time": "2019-01-01",
        "page_size": 10000,
        "ignore_forbidden_errors": "true",
    }
    next_url = None
    while True:
        if next_url:
            with urllib.request.urlopen(
                urllib.request.Request(next_url, headers={"User-Agent": "curl/8"}),
                timeout=60,
            ) as r:
                d = json.load(r)
        else:
            d = _req("/timeseries/asset-metrics", params)
        rows.extend(d.get("data", []))
        next_url = d.get("next_page_url")
        if not next_url:
            break
        time.sleep(0.7)  # community rate limit
    if not rows:
        return None
    rec = []
    for r in rows:
        e = {"date": r["time"][:10]}
        for m, c in COL.items():
            v = r.get(m)
            e[c] = float(v) if v is not None else None
        rec.append(e)
    schema = {"date": pl.Utf8, **{c: pl.Float64 for c in COL.values()}}
    return pl.DataFrame(rec, schema=schema)


def main() -> None:
    amap = overlap_assets()
    print(f"{len(amap)} overlap assets: {sorted(amap)}", flush=True)
    frames = []
    for i, (cm_id, asset) in enumerate(sorted(amap.items()), 1):
        try:
            df = fetch_asset(cm_id)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{len(amap)}] {cm_id} FAILED: {e}", flush=True)
            time.sleep(2)
            continue
        if df is None or df.height == 0:
            print(f"  [{i}/{len(amap)}] {cm_id} empty", flush=True)
            continue
        frames.append(df.with_columns(pl.lit(asset).alias("asset")))
        print(f"  [{i}/{len(amap)}] {cm_id} -> {asset}  {df.height} days "
              f"{df['date'].min()}..{df['date'].max()}", flush=True)
        time.sleep(0.7)
    panel = pl.concat(frames, how="diagonal").sort(["asset", "date"])
    panel.write_parquet(OUT)
    print(f"wrote {OUT}  rows={panel.height} assets={panel['asset'].n_unique()}", flush=True)


if __name__ == "__main__":
    main()
