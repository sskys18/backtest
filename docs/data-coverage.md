# Data Coverage Matrix - Scanner Replication

Maps each akakin83 scanner plus londonpotato1/kimchi-audit-picks to the bt
data types it requires.

## Per-scanner requirements

| Scanner | Required types | Status after this plan |
|---------|----------------|------------------------|
| alpha-hunter | OI hist, funding, trades (CVD), depth_snap (MI proxy) | covered |
| alpha-terminal | OHLCV, OI hist, funding, trades, liquidations (live), F&G | covered; liquidations are live-only |
| kimp-wave | OHLCV (Upbit, Bithumb, Binance), FX | covered |
| liqpressuremap | liquidations (live), OI hist, lsr_top_pos | covered; liquidations are live-only |
| nahonja-scanner | OHLCV, funding, OI hist, taker_ratio, liquidations (live) | covered; liquidations are live-only |
| squeeze-hunter | OI hist, trades (CVD), liquidations (live), lsr_top_pos | covered; liquidations are live-only |
| kimchi-audit-picks | OHLCV (Upbit/Bithumb), aggregated coin metadata | OHLCV covered; metadata is manual import |

## Per-type CLI examples

```bash
# Binance Futures
bt data fetch --pair BTC-USDT --source binance --type ohlcv --timeframe 1h --start 2024-01-01 --end 2024-12-31
bt data fetch --pair BTC-USDT --source binance --type funding --start 2024-01-01 --end 2024-12-31
bt data fetch --pair BTC-USDT --source binance --type oi_hist --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type lsr_top_pos --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type lsr_top_acct --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type lsr_global --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type taker_ratio --timeframe 5m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-USDT --source binance --type trades --timeframe 1m --start 2026-04-01 --end 2026-04-02
bt data fetch --pair BTC-USDT --source binance --type depth_snap

# Korean exchanges
bt data fetch --pair BTC-KRW --source upbit --type ohlcv --timeframe 1m --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC-KRW --source bithumb --type ohlcv --timeframe 1m --start 2026-04-01 --end 2026-04-30

# Macro
bt data fetch --pair USD-KRW --source fx --type fx --start 2026-04-01 --end 2026-04-30
bt data fetch --pair BTC --source fng --type fng --start 2024-01-01 --end 2026-04-30
bt data fetch --pair BTC --source onchain --type onchain --start 2024-01-01 --end 2026-04-30

# Liquidations live daemon
bt collect-liq --flush 60
```

## Limits

- Binance OI hist, LSR, and taker_ratio: Binance exposes a 30-day rolling
  window. For longer local history, run incremental fetches and append.
- Liquidations: Binance removed historical liquidations REST in 2021. Use
  `bt collect-liq` for live capture. Historical data requires an external
  paid feed and is intentionally out of scope.
- Depth: `depth_snap` is a current snapshot only. It does not reconstruct a
  historical order book.
- FX: ExchangeRate-API provides current-day rates. Historical USD/KRW uses
  Upbit USDT-KRW close as a free proxy.

## Liquidations source matrix

| Source | Coverage | Cost |
|--------|----------|------|
| Binance REST `/forceOrders` | Removed 2021 | Free |
| Binance Vision `liquidationSnapshot/` | Empty | Free |
| Binance WS `!forceOrder@arr` | Live forward only | Free; needs `bt collect-liq` daemon |
| Coinalyze | Historical | Free tier with `COINALYZE_API_KEY` env |
| Coinglass v3 | Historical | Free tier with `COINGLASS_API_KEY` env |
| CryptoQuant / Kaiko | Historical | Paid; out of scope |

## Bonus historical sources

| Source | Coverage | Notes |
|--------|----------|-------|
| `binance_vision metrics` | 1-min OI, top-trader LSR, taker ratio | Extends the 30-day REST limit |
| `binance_vision book_depth_hist` | Historical orderbook snapshots | Replaces snapshot-only `depth_snap` for historical runs |
