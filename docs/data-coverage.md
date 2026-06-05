# Data Coverage Matrix - Scanner Replication

> **Note (2026-06-04):** Data *fetching* was removed from this repo — it's now
> import-only (`bt data import`). The coverage below describes data the bt store
> consumes; producing it (exchange fetch) lives in `korea-stock` / `upbit`
> siblings. Source-adapter references here are historical.

Maps each akakin83 scanner plus londonpotato1/kimchi-audit-picks to the bt
data types it requires.

## Per-scanner requirements

| Scanner | Required types | Status |
|---------|----------------|--------|
| alpha-hunter | OI hist, funding, trades (CVD), depth_snap | ✅ Fully covered |
| alpha-terminal | OHLCV, OI hist, funding, trades, liquidations, F&G | ✅ Liq via CoinAnk (historical) or live daemon |
| kimp-wave | OHLCV (Upbit/Bithumb/Binance), FX | ✅ Fully covered |
| liqpressuremap | liquidations, OI hist, lsr_top_pos | ✅ Liq via CoinAnk (historical) or live daemon |
| nahonja-scanner | OHLCV, funding, OI hist, taker_ratio, liquidations | ✅ Liq via CoinAnk (historical) or live daemon |
| squeeze-hunter | OI hist, trades (CVD), liquidations, lsr_top_pos | ✅ Liq via CoinAnk (historical) or live daemon |
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

# Liquidations historical (no signup, free)
bt data fetch --pair BTC-USDT --source coinank --type liquidations --timeframe 4h --start 2026-04-02 --end 2026-05-02   # 30 days at 4h
bt data fetch --pair BTC-USDT --source coinank --type liquidations --timeframe 12h --start 2026-02-01 --end 2026-05-02  # 90 days at 12h
bt data fetch --pair BTC-USDT --source coinank --type liquidations --timeframe 1d --start 2025-11-01 --end 2026-05-02   # 180 days at 1d
COINANK_EXCHANGE=Binance bt data fetch --pair BTC-USDT --source coinank --type liquidations --timeframe 4h --start 2026-04-02 --end 2026-05-02

# Liquidations live daemon (real-time, infinite forward)
bt collect-liq --flush 60
```

## Limits

- Binance OI hist, LSR, and taker_ratio: Binance exposes a 30-day rolling
  window. For longer local history, run incremental fetches and append.
- Liquidations: Binance removed historical liquidations REST in 2021.
  Three free options now available:
    - `coinank` source: historical aggregated long/short turnover per
      exchange. Up to 180 days at 1d granularity, 30 days at 4h, 7.5 days
      at 1h. No signup required.
    - `bt collect-liq` daemon: live per-event Binance WS capture, infinite
      forward.
    - `coinalyze`/`coinglass` adapters: historical with free-tier API key.
- Depth: `depth_snap` is a current snapshot only. It does not reconstruct a
  historical order book.
- FX: ExchangeRate-API provides current-day rates. Historical USD/KRW uses
  Upbit USDT-KRW close as a free proxy.

## Liquidations source matrix

| Source | Coverage | Auth | Cost |
|--------|----------|------|------|
| **CoinAnk** (`coinank`) | **180d daily / 90d 12h / 30d 4h aggregated, all major exchanges** | Embedded browser key | **Free, no signup** |
| Binance WS (`bt collect-liq`) | Live forward only, per-event | None | Free |
| Coinalyze (`coinalyze`) | Historical | `COINALYZE_API_KEY` env | Free tier with signup |
| Coinglass v3 (`coinglass`) | Historical | `COINGLASS_API_KEY` env | Free tier with signup |
| Binance REST `/forceOrders` | — | — | Removed 2021 |
| Binance Vision `liquidationSnapshot/` | — | — | Empty (confirmed 2026-05-02) |
| Coinglass internal `capi.coinglass.com` | Historical | Browser key | AES-encrypted; not usable from curl |
| CryptoQuant / Kaiko | Historical | Paid | Out of scope |
| OKX `/public/liquidation-orders` | Last 24-48h, per-event | Public | User-excluded from this project |

## Bonus historical sources

| Source | Coverage | Notes |
|--------|----------|-------|
| `binance_vision metrics` | 1-min OI, top-trader LSR, taker ratio | Extends the 30-day REST limit |
| `binance_vision book_depth_hist` | Historical orderbook snapshots | Replaces snapshot-only `depth_snap` for historical runs |
