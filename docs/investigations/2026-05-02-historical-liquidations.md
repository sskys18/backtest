# Historical Liquidations Data — Investigation

Last updated: 2026-05-02 (Playwright deep-scan of public dashboards).

## Sources tested

| Source | Endpoint | Auth | Coverage | Status |
|--------|----------|------|----------|--------|
| Binance REST | `/fapi/v1/forceOrders` | Public | — | ❌ Removed 2021 |
| Binance Vision | `liquidationSnapshot/` | Public | — | ❌ Empty (confirmed S3 list) |
| Binance WS | `!forceOrder@arr` | Public | Forward only | ✅ Wired in `bt collect-liq` |
| **CoinAnk** | `/api/liquidation/statistic` | Browser key (free) | **180 buckets × interval** | ✅ **Primary historical source** |
| Coinalyze | `/v1/liquidation-history` | API key (free signup) | 30+ days | ⚠️ Requires `COINALYZE_API_KEY` |
| Coinglass v3 | `/api/futures/liquidation/history` | API key (free signup) | Plan-gated | ⚠️ Requires `COINGLASS_API_KEY` |
| Coinglass internal | `capi.coinglass.com/...` | Browser key | Encrypted response | ❌ AES-encrypted, JS-decoded only |
| Coinalyze internal | `coinalyze.net/...` | — | Static page | ❌ No public REST |
| Datamish | `datamish.com:6002/socket.io` | — | Live | ⚠️ WS only, custom protocol |
| Velo Data | `velodata.app` | — | — | ❌ Login wall |
| Hyblock | `hyblockcapital.com/dashboard` | — | — | ❌ Login wall |
| LiqMaps | `liqmaps.io` | — | — | ❌ DNS not resolved |
| CryptoMeter | `cryptometer.io/api` | — | — | ❌ 404 |
| CryptoQuant | `/v1/...` | Paid | — | ❌ Out of scope |
| Kaiko / Tardis | `api.tardis.dev` | Paid | — | ❌ Out of scope |
| OKX | `/api/v5/public/liquidation-orders` | Public | 24-48h | ⛔ User-excluded |

## Investigation method

Used Playwright MCP to:
1. Navigate to each provider's liquidation page (heatmap, history, real-time list).
2. Capture network requests + console messages with `browser_network_requests` and `browser_console_messages`.
3. Extract endpoint URL, request headers (especially auth shape), and response body shape.
4. Replay captured requests via `curl` from the host shell to verify they work outside the browser.
5. For each working endpoint, probe pagination params (`endTime`, `ts`, `size`, `side`) to map coverage limits.

Findings file dump: `~/.claude/projects/.../mcp-plugin_playwright_playwright-browser_network_request-*.txt`.

## CoinAnk — primary winner

**Endpoint**: `GET https://api.coinank.com/api/liquidation/statistic?baseCoin=<COIN>&exchangeName=&interval=<INTERVAL>`

**Auth**: requires header `coinank-apikey: <embedded browser key>`. Default key (subject to rotation):
```
coinank-apikey: ***REMOVED***
client: web
web-version: 102
Origin: https://coinank.com
Referer: https://coinank.com/
```

**Coverage** (180-bucket cap, no pagination):

| Interval | Span |
|----------|------|
| 5m | 15 hours |
| 15m | 45 hours |
| 30m | 3.75 days |
| 1h | 7.5 days |
| 4h | **30 days** |
| 12h | **90 days** |
| 1d | **180 days** |

**Schema** (per bucket):
```json
{
  "ts": 1777712400000,
  "details": {
    "Binance":     {"longTurnover": 124446, "shortTurnover": 356747, "longAmount": 1.59, "shortAmount": 4.55},
    "Bybit":       {"longTurnover": 16923,  "shortTurnover": 18696,  ...},
    "Okex":        {...}, "Huobi": {...}, "Hyperliquid": {...}, "Aster": {...}
  },
  "all": {"longTurnover": 154747, "shortTurnover": 736396, ...}
}
```

**Rate limit**: ~tens of req/min per IP, then `403 system error` cooldown for several minutes. Adapter caches one fetch per (coin, interval) call; for repeated runs, store output to Parquet via `bt data fetch`.

**Bonus endpoints exposed by same key**:
- `/api/liqMap/getLiqHeatMap?exchangeName=Binance&symbol=BTCUSDT&interval=3d` — price-bucketed liquidation cluster (matches `liqpressuremap` scanner).
- `/api/liquidation/queryNestLiq?baseCoin=BTC&interval=1d` — last-24h per-exchange breakdown.
- `/api/Statistics/all` — global totals.
- `/api/kline/list?exchange=Binance&symbol=BTCUSDT&interval=15m` — OHLCV (alternative to Binance direct).

## Coinglass — encrypted, skipped

`https://capi.coinglass.com/api/futures/liquidation/chart?...` returns **AES-encrypted base64 string** in `data` field (header `encryption: true`). Decryption key is embedded in their JavaScript bundle and rotates via `cache-ts-v2` timestamp. Out of scope to reverse-engineer; better to use their official paid API.

## Decisions

1. **Primary historical**: `coinank` source (`bt.data.sources.coinank`). Free, no signup. Use for all 4 APPROXIMATION scanner tuning runs.
2. **Forward capture**: keep `bt collect-liq` daemon (Binance WS). Better resolution (per-event) than CoinAnk's 5m+ buckets.
3. **Optional fallbacks**: `coinalyze` and `coinglass` adapters remain key-gated.
4. **Per-exchange filter**: env `COINANK_EXCHANGE=Binance` (or any of: Bybit/Okex/Huobi/Hyperliquid/Aster/Lighter/Bitfinex) to narrow turnover to one exchange. Default = sum across all exchanges.
5. **Granularity matrix for backtests**:
   - 5m bar scanner backtest, ≤15h: use `interval=5m` directly.
   - 5m bar, days–weeks: fetch `interval=4h` and forward-fill to 5m bars (degraded but usable).
   - Daily/weekly aggregates: fetch `interval=1d` for 6 months out of the box.

## Future work

- CoinAnk pagination is server-ignored. To extend beyond 180 buckets, run periodically and accumulate. Add a `bt collect-coinank` daemon mirroring `bt collect-liq`.
- Reverse-engineer Coinglass AES decryption only if their paid API becomes prohibitive.
- Validate CoinAnk per-exchange long/short totals against `bt collect-liq` Binance WS captures over a 1-week window (sanity check).
