# data/raw — reproduction guide

Everything here is **gitignored**. Git history keeps the *recipes*, not the
data. Each deep panel below is fully refetchable with one command.

## Research-grade panels (deep)

| file | contents | rebuild |
|---|---|---|
| `binance_funding_panel.parquet` | 530 USDT perps, funding events 2019-09→now (1.9 M rows; API paginates to full depth) | `python scripts/experiments/fetch_binance_funding_panel.py` |
| `binance_perp_panel_1d.parquet` | same universe, daily perp OHLCV + quote_vol | same script (one run writes both) |
| `coinmetrics_onchain_1d.parquet` | 34 majors, daily AdrActCnt/TxCnt/TxTfrCnt/MVRV/mcap 2019-01→now | `python scripts/experiments/fetch_coinmetrics_onchain.py` (needs perp panel first, for the universe) |
| `binance_spot_panel_1d.parquet` + `binance_spot_btc_1d.parquet` | 414 spot alts daily, 18 mo, $1 M liq floor; BTC held out | `python scripts/experiments/fetch_binance_spot_panel.py` |
| `binance_spot_panel_5m.parquet` + `binance_spot_btc_5m.parquet` | 155 liquid alts 5m 60 d | `python scripts/experiments/fetch_binance_intraday.py` |
| `krx/prices.parquet` | 2,650 KR equities daily 2019-07→now | produced by `korea-stock` sibling → `bt data import` |
| `dart/events.parquet` | 290 k DART disclosure events | produced by `korea-stock` sibling → `bt data import` |

## Capped / scrap data (NOT research-grade)

- `binance_micro_*.parquet` — taker/OI panels; Binance free endpoints cap at
  ~500 points (5m≈1.7 d, 1h≈21 d) regardless of startTime. Deep history is
  NOT obtainable — live collection only. Rebuild (shallow):
  `python scripts/experiments/fetch_binance_micro.py`.
- `binance/<PAIR>/...`, `binance_vision/`, `coinank/`, `fng/`, `onchain/BTC/`
  — single-shot snapshots from the April scanner era (≤30 d each). Superseded
  by the panels above where applicable.
- `upbit/<PAIR>/ohlcv/1m` — 9,600-bar (≈6.7 d) scan-replay windows; 1h ×7
  coins ≈7 mo. Refetch via the `upbit` sibling.

## Panel-store convention

Core store layout: `data/raw/{source}/{pair}/{type}/data.parquet`, ingested
only via `bt data import` (core never fetches). Experiment fetchers under
`scripts/experiments/` write top-level panel parquets directly — throwaway by
design.
