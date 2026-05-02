# Historical Liquidations Data - Investigation

## Sources tested

| Source | Path/endpoint | Status | Notes |
|--------|---------------|--------|-------|
| Binance REST | `/fapi/v1/forceOrders` | Removed 2021 | No alternative on REST |
| Binance Vision | `data/futures/um/daily/liquidationSnapshot/` | Empty | Confirmed via S3 list 2026-05-02 |
| Binance WS | `wss://fstream.binance.com/ws/!forceOrder@arr` | Live only | Already wired in `bt collect-liq` |
| Coinalyze | `/v1/liquidation-history` | Free tier with key | Requires `COINALYZE_API_KEY` env |
| Coinglass v3 | `/api/futures/liquidation/history` | Free tier with key | Requires `COINGLASS_API_KEY` env |
| CryptoQuant | `/v1/...` | Paid only | Out of scope |
| Kaiko | tier-gated | Paid only | Out of scope |

## Decision

1. Keep `bt collect-liq` daemon as primary forward-capture path.
2. Add `coinalyze` and `coinglass` source adapters that read API keys from env. Without a key, they raise a clear configuration error.
3. Document the live-only Binance liquidation constraint in `docs/data-coverage.md` and per-scanner docs.
4. Make liquidation features optional where practical. Feature computation degrades to zeroed liquidation factors when liquidation columns are absent.

## Bonus discoveries during investigation

- Binance Vision `data/futures/um/{daily,monthly}/metrics/` returns historical per-minute OI, taker LSR, and taker buy/sell volume ratio, extending the 30-day REST limit.
- Binance Vision `data/futures/um/{daily,monthly}/bookDepth/` provides historical orderbook snapshots, replacing snapshot-only `depth_snap` with multi-day history.

These are wired through the `binance_vision` data source.
