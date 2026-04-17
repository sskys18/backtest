# Session Handoff
> Updated: 2026-04-17

## Task
Build a hybrid Rust+Python crypto backtesting framework (`bt` CLI) with scan replay signal optimization for Korean exchange altcoin trading.

## Status — All Phases Complete

| Phase | What | Tests |
|-------|------|-------|
| 1 MVP | Rust engine + vectorized strategies + CLI | 28 Rust + 21 Python |
| 1.5 | Exchange integration (Upbit/Bithumb/Binance) | Verified live |
| 2 | TimesFM forecast integration | 4 Python |
| 3 | Optuna Bayesian optimizer | 6 Python |
| 4 | PPO RL (Rust step API + Gymnasium env) | 5 Rust + 5 Python |
| 5 | Scan replay backtester | Verified on 18,550 signals |
| **Total** | | **28 Rust + 36 Python = 64 tests** |

## Primary Workflow: Scan Replay

The main use case is optimizing entry/exit rules for the Upbit trading bot:

```bash
# Fetch 1min OHLCV for all scanned coins
# (already fetched 50 markets, data in data/raw/upbit/)

# Run optimization
bt replay --log ../upbit/data/trades.jsonl --ohlcv-dir data/raw
```

### Latest Optimization Results (18,550 signals, 50 markets)

**Best entry filter:** `change_24h < 3% + rsi_15m > 60 + score > 35`
**Best exit:** TP=2.0% SL=1.0% MaxHold=240min
**Performance:** 60% WR, +0.75% avg PnL, Sharpe 0.51

### Key Insight
Bot's composite `score` alone has no alpha (r=+0.005). The edge is in signal combinations — specifically, entering **calm coins with building momentum** (low change_24h + high RSI), not coins already pumping.

## Secondary Workflow: OHLCV Backtest

Standard candle-based backtesting with Rust engine:

```bash
bt data fetch --pair BTC-USDT --source binance --type ohlcv --timeframe 1h --start 2024-06-01 --end 2025-01-01
bt backtest --config configs/strategies/momentum_v1.yaml
bt optimize --config configs/strategies/momentum_v1.yaml --space search_space.yaml --method bayesian --budget 30
```

## Project Structure

```
backtest/
├── crates/bt-engine/src/     # Rust: engine, step API, PyO3 bindings
├── src/bt/
│   ├── replay/               # Scan replay backtester (PRIMARY)
│   ├── data/sources/         # Upbit, Bithumb, Binance adapters
│   ├── strategy/             # momentum, mean_revert, timesfm_signal, ppo
│   ├── optimize/             # grid, random, bayesian (Optuna)
│   ├── rl/                   # PPO Gymnasium env + training
│   └── ...                   # config, cli, backtest, features, report, experiment
├── data/raw/upbit/           # 1min OHLCV for 50+ KRW altcoins
├── experiments/              # Replay results, goals.md, leaderboard
└── docs/                     # Architecture, specs, plans
```

## Resume Here

1. **More data** — run bot longer, accumulate more trades.jsonl data, rerun `bt replay`
2. **Apply rules to live bot** — encode optimal filters into bot config
3. **Continuous optimization** — use Claude Code `/loop` with `bt replay` to auto-optimize as new data comes in
4. **Bithumb expansion** — same replay approach for Bithumb scan data

## Decisions
- Hybrid Rust+Python: Rust simulation, Python orchestration
- PyO3 0.28 for Python 3.14, `python` feature gate for clean cargo test
- exchange-adapters as editable dep from sibling dir
- Scan replay is primary workflow (microstructure alpha), OHLCV backtest is secondary
- leaderboard.json for experiment tracking (not SQLite)

## Gotchas
- Upbit API: 200 bars per page, paginate backwards from `to` datetime
- Upbit adapter needs PyJWT (`uv pip install PyJWT`)
- Binance class name: `BinanceFuturesUssdtRestClient` (double 's' — typo in adapter)
- `uv sync` can remove test deps — re-install with `uv pip install pytest`
- maturin not in uv sync — install separately with `uv pip install maturin`
