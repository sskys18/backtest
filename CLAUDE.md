# bt — Quant Backtesting & Signal Optimization Framework

Hybrid Rust+Python system for backtesting quant strategies. Two lineages: (1) a **general multi-asset quant backtest engine** (cross-sectional / long-short / portfolio, intraday + daily, perp + spot) and (2) crypto microstructure signal optimization on Korean exchanges (Upbit, Bithumb).

## Goal

General multi-asset quant backtesting (Approach A: panel-vectorized signal → event-driven portfolio; KR-perp long-short = first reference strategy; spec `docs/specs/2026-06-04-multi-asset-quant-engine-design.md`) **plus** finding alpha for early-stage KRW altcoins on Upbit/Bithumb (microstructure signals — orderbook imbalance, whale activity, volume surges).

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

## Three Backtesting Modes

### 1. Panel Backtest (multi-asset quant — general engine)
```bash
bt backtest-panel --panel data/raw/panel.parquet --config configs/panel/kr_sector_rotation_daily.yaml
```
Multi-asset panel `(T×N)` → signed target weights `W∈[−1,+1]` → event-driven portfolio (signed perp positions, funding, margin, session/risk rules) → walk-forward OOS (purged CV + DSR/PBO). Cross-sectional / long-short / pairs, intraday + daily. Rust `PortfolioEngine` (`run_panel`). See `src/bt/panel/`.

### 2. OHLCV Backtest (single-asset strategies on candle data)
```bash
# Data fetching lives in sibling repos (korea-stock / upbit). Import parquet here:
bt data import --path /path/to/BTC-USDT-1h.parquet --type ohlcv
bt backtest --config configs/strategies/momentum_v1.yaml
```

### 3. Scan Replay (crypto microstructure signal optimization)
```bash
bt replay --log ../upbit/data/trades.jsonl --ohlcv-dir data/raw
```
Replays bot scan events against 1min OHLCV bars, tests all entry filter + exit param combinations, finds optimal alpha.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Panel Quant Engine (bt backtest-panel)     │  ← general multi-asset
│  panel (T×N) → signed W → event-driven       │
│  portfolio → walk-forward OOS (DSR/PBO)      │
│  src/bt/panel/ + Rust run_panel()            │
├─────────────────────────────────────────────┤
│  Scan Replay Engine (bt replay)             │  ← crypto microstructure
│  18k+ signal observations → filter sweep    │
│  → TP/SL optimization → optimal rules       │
├─────────────────────────────────────────────┤
│  OHLCV Backtest Engine (bt backtest)        │  ← single-asset strategies
│  Python CLI → Strategy → Rust Engine         │
├─────────────────────────────────────────────┤
│  Data: IMPORT-ONLY (no fetching here)       │
│  parquet via `bt data import` (siblings own │
│  fetching: korea-stock / upbit)             │
├─────────────────────────────────────────────┤
│  Rust Core (crates/bt-engine/)              │
│  tick(), run(), step(), run_panel() — PyO3   │
└─────────────────────────────────────────────┘
```

## Project Layout

- `crates/bt-engine/src/` — Rust engine (types, broker, portfolio, risk, tick, engine, step, **portfolio_engine** = perp panel engine, python bindings incl. `run_panel`)
- `src/bt/panel/` — Multi-asset panel engine (loader, session, strategy, primitives, run, metrics, stats, walkforward, bias, report)
- `src/bt/replay/` — Scan replay backtester (loader, simulator, optimizer)
- `src/bt/` — Python package (cli, config, data, features, strategy, backtest, report, experiment, optimize, rl)
- `src/bt/data/` — Import-only data layer (import_, store, validate, schemas). No fetching — siblings own that.
- `src/bt/rl/` — PPO reinforcement learning (env, train, policy_strategy)
- `src/bt/scanners/` — 7 live web-scanner ports (kimp_wave, squeeze_hunter, …) + Optuna tune CLI; see `docs/scanners.md`
- `configs/` — YAML strategy configs (incl. `configs/panel/` reference panel configs)
- `tests/python/` — Python tests (165 tests)
- `scripts/live/` — standing tools (heat_screener, divergence alert + plist, scanner feature builder)
- `scripts/experiments/` — one-off frozen fetcher/backtest pairs for the alpha ledger
- `experiments/` — **README.md = alpha ledger** (every investigation + verdict), date-stamped immutable reports, goals.md, archive/
- `data/raw/` — Parquet data store (gitignored); `data/raw/README.md` = per-panel reproduction commands

## Key Conventions

- Rust crate uses feature `python` for PyO3 bindings (off by default for `cargo test`)
- PyO3 0.28 / numpy 0.28 for Python 3.14
- Import-only **core**: `src/bt/data/` never fetches; siblings (korea-stock / upbit) produce parquet, `bt data import` ingests it. One-off experiment fetchers live under `scripts/` only (throwaway, not wired into `bt.data`)
- Polars for DataFrames, NumPy only at Rust boundary
- Strategies return `strength` + `target_size` columns
- All data stored as Parquet under `data/raw/{source}/{pair}/{type}/`

## CLI Commands

| Command | Purpose |
|---------|---------|
| `bt backtest-panel` | Multi-asset panel quant backtest (general engine) |
| `bt replay` | Scan replay optimization (crypto microstructure) |
| `bt backtest` | OHLCV single-asset backtest with strategy |
| `bt data import` | Import parquet/CSV into the store |
| `bt data list` | List datasets |
| `bt data validate` | Check data quality |
| `bt features` | Build feature DataFrame |
| `bt optimize` | Grid/random/bayesian param search |
| `bt train` | PPO RL training |
| `bt report` | Generate run report |
| `bt strategies` | List registered strategies |

## Strategies

- **Single-asset** (`bt backtest`): momentum, mean_revert, timesfm_signal, ppo
- **Panel** (`bt backtest-panel`): `CrossSectionalStrategy` (§4.1 rank → top-k/bottom-k → vol-scale → sector-neutralize → cap), `PairsStrategy` (§4.2 dollar-neutral spread z-score). Both `PanelStrategy` implementations; trailing-only helpers + `detect_lookahead` canary (C4).

## Data Types

OHLCV, funding, OI, depth, trades, liquidations, cross_exchange, fx, records — schemas in `src/bt/data/schemas.py`
