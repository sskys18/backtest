#!/usr/bin/env python
"""Live 'heat' screener — rank liquid Binance perps by how much is happening NOW.

DESCRIPTIVE monitoring, NOT prediction. Surfaces coins with volume/OI/breakout/
funding spikes so a human judges entry. Backtests in this repo show these signals
do NOT predict forward returns — this tool tells you WHAT is moving, not whether
it will continue. Entering after a coin is already 'hot' is the documented
reversal trap; use this to not MISS a move, not to chase blindly.

Usage: heat_screener.py [min_24h_vol_usd]   (default $100M)
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

import numpy as np

F = "https://fapi.binance.com"


def get(path, params):
    url = F + path + ("?" + urllib.parse.urlencode(params) if params else "")
    return json.load(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "curl/8"}), timeout=30))


def main():
    min_vol = float(sys.argv[1]) if len(sys.argv) > 1 else 1e8
    tick = {t["symbol"]: t for t in get("/fapi/v1/ticker/24hr", {})}
    info = get("/fapi/v1/exchangeInfo", {})
    perp = [s["symbol"] for s in info["symbols"]
            if s.get("contractType") == "PERPETUAL" and s["quoteAsset"] == "USDT"
            and s["status"] == "TRADING"]
    universe = [s for s in perp if s in tick and float(tick[s]["quoteVolume"]) >= min_vol]
    print(f"scanning {len(universe)} liquid perps (>= ${min_vol/1e6:.0f}M/24h)...\n", flush=True)

    rows = []
    for sym in universe:
        try:
            kl = get("/fapi/v1/klines", {"symbol": sym, "interval": "1h", "limit": 200})
            oih = get("/futures/data/openInterestHist",
                      {"symbol": sym, "period": "1h", "limit": 30})
            fr = get("/fapi/v1/premiumIndex", {"symbol": sym})
        except Exception:
            continue
        if len(kl) < 50:
            continue
        close = np.array([float(r[4]) for r in kl])
        qvol = np.array([float(r[7]) for r in kl])     # quote volume per 1h
        # vol surge: last 6h mean vs trailing 7d (168h) mean
        base = qvol[-168:-6].mean() if len(qvol) > 174 else qvol[:-6].mean()
        vol_surge = qvol[-6:].mean() / base if base > 0 else 0.0
        # breakout: close vs trailing 7d high (excl now)
        hi = close[-168:-1].max() if len(close) > 168 else close[:-1].max()
        breakout = close[-1] / hi if hi > 0 else 0.0
        ret24 = close[-1] / close[-25] - 1 if len(close) > 25 else 0.0
        ret6 = close[-1] / close[-7] - 1 if len(close) > 7 else 0.0
        # OI change 24h
        oi_chg = 0.0
        if len(oih) >= 25:
            oiv = np.array([float(r["sumOpenInterestValue"]) for r in oih])
            oi_chg = oiv[-1] / oiv[-25] - 1 if oiv[-25] > 0 else 0.0
        funding = float(fr["lastFundingRate"]) if "lastFundingRate" in fr else 0.0
        # composite heat: standardized-ish blend (volume surge dominates)
        heat = (max(vol_surge - 1, 0) * 1.0
                + max(oi_chg, 0) * 3.0
                + max(breakout - 1, 0) * 20.0
                + abs(funding) * 100.0)
        rows.append((sym.replace("USDT", ""), heat, vol_surge, oi_chg, breakout,
                     ret24, ret6, funding, float(tick[sym]["quoteVolume"])))
        time.sleep(0.05)

    rows.sort(key=lambda r: -r[1])
    print(f"{'coin':10}{'HEAT':>6}{'volX':>6}{'OI24h':>8}{'brkout':>8}"
          f"{'ret24h':>8}{'ret6h':>8}{'funding':>9}{'$24h':>8}")
    print("-" * 80)
    for r in rows[:25]:
        c, heat, vx, oi, brk, r24, r6, fnd, qv = r
        flag = "NEW-HI" if brk >= 1.0 else ""
        print(f"{c:10}{heat:6.1f}{vx:6.1f}{oi:+8.1%}{brk:8.3f}"
              f"{r24:+8.1%}{r6:+8.1%}{fnd:+9.4f}{qv/1e9:7.1f}B {flag}")
    print("\nvolX=6h vol / 7d-avg | OI24h=open-interest chg | brkout=close/7d-high "
          "(>=1.0 = new high) | funding>0 = longs crowded")

    # Fresh HEALTHY up-breakout: volume spike + rising OI + near/at new high +
    # positive return. Distinguishes a pump-in-progress from a crash (which also
    # spikes 'heat'). Still NOT a guarantee — just a cleaner watch list.
    fresh = [r for r in rows if r[2] >= 2.0 and r[3] > 0 and r[4] >= 0.97
             and r[5] > 0]  # volX>=2, oi_chg>0, breakout>=0.97, ret24h>0
    print("\n=== FRESH HEALTHY UP-MOVES (volX>=2, OI rising, near/at 7d high, ret24h>0) ===")
    if fresh:
        for r in sorted(fresh, key=lambda x: -x[1]):
            print(f"  {r[0]:10} volX={r[2]:.1f} OI24h={r[3]:+.0%} "
                  f"brkout={r[4]:.3f} ret24h={r[5]:+.0%} ret6h={r[6]:+.0%}")
    else:
        print("  (none right now — nothing is in a clean fresh breakout)")
    print("\nREAD: heat = something happening NOW (incl. CRASHES — ZEC tops the list "
          "while -45%). NOT a buy signal; late entry = reversal trap (experiments/).")
    print("To catch movers: run on a schedule, alert when a coin ENTERS the fresh-"
          "up-moves list. You still judge entry + risk.")


if __name__ == "__main__":
    main()
