# bt — Crypto Backtesting & Signal Optimization Framework

Hybrid Rust+Python system for backtesting crypto strategies and optimizing microstructure trading signals on Korean exchanges (Upbit, Bithumb).

## Primary Goal

Find alpha for early-stage KRW altcoins on Upbit/Bithumb. The edge is in microstructure signals (orderbook imbalance, whale activity, volume surges), not price-only indicators.

## Build & Test

```bash
# Rust tests (no Python needed)
cargo test -p bt-engine

# Python setup + build extension
uv venv && source .venv/bin/activate
uv pip install maturin pytest optuna gymnasium stable-baselines3 PyJWT
maturin develop

# Python tests
uv run pytest tests/python/ -v

# CLI
uv run bt --help
uv run bt strategies
```

## Two Backtesting Modes

### 1. OHLCV Backtest (generic strategies on candle data)
```bash
bt data fetch --pair BTC-USDT --source binance --type ohlcv --timeframe 1h --start 2024-06-01 --end 2025-01-01
bt backtest --config configs/strategies/momentum_v1.yaml
```

### 2. Scan Replay (microstructure signal optimization)
```bash
bt replay --log ../upbit/data/trades.jsonl --ohlcv-dir data/raw
```
Replays bot scan events against 1min OHLCV bars, tests all entry filter + exit param combinations, finds optimal alpha. This is the primary workflow.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Scan Replay Engine (bt replay)             │  ← primary workflow
│  18k+ signal observations → filter sweep    │
│  → TP/SL optimization → optimal rules       │
├─────────────────────────────────────────────┤
│  OHLCV Backtest Engine (bt backtest)        │  ← secondary workflow
│  Python CLI → Strategy → Rust Engine        │
├─────────────────────────────────────────────┤
│  Data Sources                               │
│  Upbit, Bithumb, Binance (exchange-adapters)│
├─────────────────────────────────────────────┤
│  Rust Core (crates/bt-engine/)              │
│  tick(), run(), step() — PyO3/NumPy         │
└─────────────────────────────────────────────┘
```

## Project Layout

- `crates/bt-engine/src/` — Rust engine (types, broker, portfolio, risk, tick, engine, step, python bindings)
- `src/bt/replay/` — Scan replay backtester (loader, simulator, optimizer)
- `src/bt/` — Python package (cli, config, data, features, strategy, backtest, report, experiment, optimize, rl)
- `src/bt/data/sources/` — Exchange adapters (binance, bithumb, upbit)
- `src/bt/rl/` — PPO reinforcement learning (env, train, policy_strategy)
- `configs/` — YAML strategy configs
- `tests/python/` — Python tests (36 tests)
- `experiments/` — goals.md + replay results + run artifacts
- `data/raw/` — Parquet data store (gitignored)

## Key Conventions

- Rust crate uses feature `python` for PyO3 bindings (off by default for `cargo test`)
- PyO3 0.28 / numpy 0.28 for Python 3.14
- exchange-adapters is an editable dependency from sibling dir
- Polars for DataFrames, NumPy only at Rust boundary
- Strategies return `strength` + `target_size` columns
- All data stored as Parquet under `data/raw/{source}/{pair}/{type}/`

## CLI Commands

| Command | Purpose |
|---------|---------|
| `bt replay` | Scan replay optimization (primary) |
| `bt backtest` | OHLCV backtest with strategy |
| `bt data fetch` | Pull data from exchanges |
| `bt data list` | List datasets |
| `bt data validate` | Check data quality |
| `bt features` | Build feature DataFrame |
| `bt optimize` | Grid/random/bayesian param search |
| `bt train` | PPO RL training |
| `bt report` | Generate run report |
| `bt strategies` | List registered strategies |

## Strategies

momentum, mean_revert, timesfm_signal, ppo

## Data Types

OHLCV, funding, OI, depth, trades, liquidations, cross_exchange, fx, records — schemas in `src/bt/data/schemas.py`
