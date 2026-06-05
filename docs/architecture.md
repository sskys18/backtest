# Architecture

## System Overview

Two primary workflows: **Scan Replay** (microstructure signal optimization) and **OHLCV Backtest** (generic strategy backtesting).

```
┌─────────────────────────────────────────────────────────────┐
│  PRIMARY: Scan Replay (bt replay)                           │
│  Bot scan logs → filter sweep → TP/SL grid → optimal rules │
├─────────────────────────────────────────────────────────────┤
│  SECONDARY: OHLCV Backtest Pipeline                         │
│                                                             │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌────────┐ │
│  │ bt data  │──▶│bt features│──▶│bt backtest│──▶│bt report│ │
│  │  fetch   │   │   build   │   │  / train  │   │        │ │
│  └────┬─────┘   └─────┬─────┘   └────┬─────┘   └────────┘ │
│       │               │              │                      │
│       ▼               ▼              ▼                      │
│  data/raw/       data/features/  experiments/runs/          │
│  *.parquet       *.parquet       {id}/metrics.json          │
├─────────────────────────────────────────────────────────────┤
│                   Python Layer (src/bt/)                     │
│                                                             │
│  config.py ─────▶ Strategy ─────▶ Backtest Orchestrator     │
│  replay/           (vectorized     (load data, run strategy, │
│  rl/               Polars)          call Rust, save results) │
├─────────────────────────────────────────────────────────────┤
│  Data: IMPORT-ONLY (bt data import → parquet store)         │
│  Fetching removed; siblings korea-stock / upbit produce it  │
├─────────────────────────────────────────────────────────────┤
│                   Rust Engine (crates/bt-engine/)            │
│                                                             │
│  NumPy arrays in ──▶ tick() loop ──▶ BacktestResult out     │
│  (bars + signals)    (broker,        (equity curve,          │
│                       portfolio,      fills, metrics)        │
│                       risk)                                  │
└─────────────────────────────────────────────────────────────┘
```

## Design Principle

**Python prepares, Rust executes, Python analyzes.**

No Python in the hot loop. Strategies are vectorized — they precompute signal columns for all bars in one pass, then Rust runs the full simulation at native speed.

## Component Boundaries

### Rust Engine (`crates/bt-engine/`)

Owns the **entire simulation loop**. Python never calls into Rust per-bar.

```
                    ┌─────────────────────────┐
                    │      BacktestEngine      │
                    │                          │
  bars[] ──────────▶│  for (bar, signal) {     │──────────▶ BacktestResult
  signals[] ───────▶│      tick(bar, signal)   │            - equity_curve
                    │  }                       │            - fills
                    │                          │            - metrics
                    └──────────┬───────────────┘
                               │
                    ┌──────────▼───────────────┐
                    │         tick()            │
                    │                          │
                    │  1. Fill pending orders   │
                    │  2. Mark to market        │
                    │  3. Check drawdown halt   │
                    │  4. Gate signal → order   │
                    │  5. Execute/enqueue order │
                    │  6. Record equity         │
                    └──────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
         ┌─────────┐   ┌───────────┐   ┌────────────┐
         │  Broker  │   │ Portfolio │   │    Risk    │
         │          │   │           │   │  Manager   │
         │ slippage │   │ positions │   │            │
         │ fees     │   │ cash      │   │ size caps  │
         │ pending  │   │ equity    │   │ DD halt    │
         │ orders   │   │ drawdown  │   │ signal gate│
         └─────────┘   └───────────┘   └────────────┘
```

**`tick()` is the shared state transition** — used by both `run()` (batch backtest) and the future `step()` API (PPO reinforcement learning). This prevents logic duplication.

### Python Layer (`src/bt/`)

```
                        ┌──────────────┐
                        │   cli.py     │  ◀── typer, JSON to stdout
                        └──────┬───────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                     ▼
    ┌───────────┐      ┌─────────────┐      ┌─────────────┐
    │  config.py│      │ backtest.py │      │ optimize/   │
    │           │      │             │      │  run.py     │
    │ YAML load │      │ orchestrate │      │  grid.py    │
    │ Pydantic  │      │ 10-step     │      │             │
    │ validate  │      │ pipeline    │      │ grid/random │
    └───────────┘      └──────┬──────┘      │ search      │
                              │             └─────────────┘
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                    ▼
    ┌───────────┐      ┌───────────┐      ┌─────────────┐
    │  data/    │      │ strategy/ │      │  report/    │
    │           │      │           │      │  experiment/│
    │ schemas   │      │ base.py   │      │             │
    │ validate  │      │ momentum  │      │ metrics     │
    │ import    │      │ mean_rev  │      │ leaderboard │
    │ store     │      │ registry  │      │ manifest    │
    └───────────┘      └───────────┘      └─────────────┘
```

## Data Flow

### Backtest Pipeline (10 steps)

```
 YAML config ──▶ load_config() ──▶ Config (validated)
                                      │
                        ┌─────────────┘
                        ▼
 features.parquet ──▶ pl.read_parquet() ──▶ DataFrame
                                              │
                        ┌─────────────────────┘
                        ▼
 Strategy ──────▶ compute(df) ──▶ signals DataFrame
                                   │ strength (f64)
                                   │ target_size (f64)
                                   ▼
                  NaN/null → 0.0 (validated)
                                   │
                        ┌──────────┘
                        ▼
 Rust Engine ◀── .to_numpy() ──── NumPy arrays (zero-copy for contiguous f64)
      │
      ▼
 BacktestResult ──▶ save to experiments/runs/{id}/
                    ├── metrics.json
                    ├── config.yaml (frozen)
                    ├── equity_curve.parquet
                    └── manifest.json (reproducibility)
```

### Data Ingestion Pipeline

```
 Parquet/CSV from sibling repos (korea-stock / upbit)
          │
          ▼
 bt data import
          │
          ▼
 bt data validate (schema check, monotonic ts, OHLC rules)
          │
          ▼
 data/raw/{source}/{pair}/{type}/*.parquet
          │
          ▼
 bt features build --config features.yaml
          │
          ├── Load OHLCV backbone
          ├── Left-join other sources by timestamp
          │   ├── forward_fill for lower-frequency data
          │   └── aggregate for trading records
          ├── Compute indicators (SMA, RSI, Bollinger, etc.)
          │
          ▼
 data/features/{pair}-{tf}.parquet (single wide DataFrame)
```

## Data Architecture

### Storage Layout
```
data/
├── raw/                              # Per-source, per-pair, per-type
│   ├── binance/BTC-USDT/
│   │   ├── ohlcv/1h/*.parquet       # Price backbone
│   │   ├── funding/*.parquet         # Funding rates
│   │   ├── oi/*.parquet              # Open interest
│   │   ├── depth/1h/*.parquet        # Order book snapshots
│   │   ├── trades/1h/*.parquet       # Aggregated tick data
│   │   └── liquidations/1h/*.parquet
│   ├── bithumb/BTC-KRW/ohlcv/1h/    # Cross-exchange
│   └── fx/KRW-USD/                   # FX rates
├── records/                          # Your own trading records
└── features/                         # Joined wide DataFrames
    └── BTC-USDT-1h-full.parquet
```

### Data Types (9 schemas)

| Type | Key Columns | Frequency |
|------|-------------|-----------|
| ohlcv | open, high, low, close, volume | bar interval |
| funding | funding_rate, mark_price | 8h (varies) |
| oi | open_interest | varies |
| depth | bid/ask prices+qtys, spread, mid | bar interval |
| trades | num_trades, buy/sell_volume, vwap | bar interval |
| liquidations | long_liq_usd, short_liq_usd | bar interval |
| cross_exchange | exchange, close, volume | bar interval |
| fx | pair, rate | varies |
| records | side, price, quantity, fee, pnl | trade-level |

All share `timestamp` (i64, Unix ms, UTC) as the join key. OHLCV is always the backbone — everything else is left-joined onto it.

## Signal Model

```
 Strategy.compute(features_df)
          │
          ▼
 ┌────────────────────────────────┐
 │  strength: f64 [0.0, 1.0]     │  conviction level
 │  target_size: f64 [0.0, 1.0]  │  fraction of equity
 └────────────────────────────────┘
          │
          ▼
 RiskManager.gate_signal()
          │
          ├── strength >= threshold → want LONG
          │   └── flat? → Buy order (size capped)
          │   └── already long? → no order (hold)
          │
          └── strength < threshold → want FLAT
              └── holding? → Sell order (full close)
              └── already flat? → no order
```

**MVP: binary long/flat only.** No shorts, no partial adjustments, no position resizing while holding.

## Order Execution

### Fill Models

| Model | When Filled | At Price | Use Case |
|-------|-------------|----------|----------|
| Immediate | Same bar | bar.close + slippage | Simple, less realistic |
| NextBar | Next bar | bar.open + slippage | Recommended for backtesting |

### Per-Bar Order of Operations (deterministic)

1. **Fill pending** — orders from previous bar filled at current bar OPEN + slippage
2. **Mark to market** — update equity, unrealized PnL at current bar CLOSE
3. **Risk check** — if drawdown >= halt threshold: emergency liquidate (always Immediate), halt
4. **Signal → order** — risk manager gates signal, generates Buy/Sell or nothing
5. **Execute** — Immediate: fill now at CLOSE. NextBar: enqueue for next bar
6. **Record equity** — snapshot for equity curve

### Risk Enforcement

| Rule | Config Key | Behavior |
|------|-----------|----------|
| Position size cap | `max_position_size` | Clamps `target_size` before order |
| Drawdown halt | `max_drawdown_halt` | Liquidate + halt when DD >= threshold |
| Signal gate | `signal_threshold` | Minimum strength to go long |
| Cash guard | (built-in) | Clamps buy quantity if cash insufficient |
| Emergency close | (built-in) | Always uses Immediate fill, ignores config |

## Experiment Loop

```
 Claude Code /loop
      │
      ├── 1. Read goals.md (objective, constraints, bounds)
      ├── 2. Read leaderboard.json (past results, current best)
      ├── 3. Check budget (count runs vs limit)
      ├── 4. Pick next config (guided by history)
      ├── 5. Write exp_{N}.yaml
      ├── 6. Run: bt backtest --config exp_{N}.yaml
      ├── 7. Parse: metrics.json from stdout
      ├── 8. Check constraints (DD < 15%, trades >= 50)
      ├── 9. Update leaderboard if feasible + better Sharpe
      └── 10. Repeat until budget exhausted
```

### Guardrails
- Only YAML config mutations allowed (no .py changes)
- Every run is immutable once written
- Budget is enforced (count dirs in experiments/runs/)
- Atomic leaderboard updates (.tmp → rename)
- Failed runs recorded with error.txt

## Technology Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Simulation | Rust + PyO3 0.28 | 10-100x faster than Python for bar iteration |
| Data transfer | NumPy arrays | Zero-copy for contiguous f64 via PyO3 numpy crate |
| DataFrames | Polars | Faster than pandas, Parquet-native, lazy eval |
| Storage | Parquet | Columnar, compressed, no DB needed |
| Config | YAML + Pydantic | Human-readable, type-validated, Claude Code can edit |
| CLI | Typer | Auto help, JSON output for machine consumption |
| Experiments | leaderboard.json | Simple, atomic, single source of truth |
| Build | uv + maturin | uv for Python deps, maturin for Rust → Python wheel |

## Scan Replay Engine

The primary alpha-finding tool. Replays bot scan events against 1min OHLCV data.

```
 trades.jsonl (bot scan log)
      │
      ├── 1,906 scans × 10 coins = 18,550 signal observations
      │   Each has: score, change_24h, volume_ratio, bid_ratio,
      │             rsi_15m, ema_cross, price, market
      │
      ▼
 Replay Simulator (src/bt/replay/)
      │
      ├── For each observation passing entry filter:
      │   1. Find 1min OHLCV bars after scan timestamp
      │   2. Walk forward bar-by-bar
      │   3. Check: did TP, SL, trailing stop, or stale timeout hit first?
      │   4. Record outcome with full signal context
      │
      ▼
 Filter Optimizer
      │
      ├── Phase 1: Test all 1/2/3-signal entry filter combos
      │   (~2000 combinations, rank by avg PnL)
      │
      ├── Phase 2: Grid search TP/SL/trail/max_hold for best filter
      │   (~360 exit configs)
      │
      └── Output: optimal entry rules + exit params + expected performance
```

## Data Sources (removed — import-only)

```
 Data FETCHING was removed from this repo (2026-06-04). It now lives in
 sibling repos:
   - korea-stock/   — KIS/KRX/Hyperliquid (KR equities + perp)
   - upbit/         — Upbit/Binance/Bithumb crypto

 This repo is import-only:
   src/bt/data/import_.py  — bt data import (parquet/CSV → store)
   src/bt/data/store.py    — normalize_and_save
   src/bt/data/validate.py — schema validation
   src/bt/data/schemas.py  — canonical schemas
```

## Reinforcement Learning (Phase 4)

```
 Rust StepEngine (crates/bt-engine/src/step.rs)
      │
      ├── step(strength, target_size) → (obs, reward, done)
      │   Reuses tick() — same state transition as batch run()
      │
      ▼
 Gymnasium BacktestEnv (src/bt/rl/env.py)
      │
      ├── Observation: [OHLCV, equity, cash, position, drawdown] (11 dims)
      ├── Action: [strength, target_size] continuous [0,1]
      ├── Reward: log return of equity
      │
      ▼
 PPO Training (src/bt/rl/train.py)
      │
      └── stable-baselines3 MlpPolicy → saved model → PPOStrategy for bt backtest
```

## Progressive ML Path

| Phase | What | Status |
|-------|------|--------|
| **1 (MVP)** | Rust engine + vectorized strategies + grid search + CLI | **Done** |
| **1.5** | Exchange integration (Upbit/Bithumb/Binance) | **Done** |
| **2** | TimesFM forecasts as feature columns | **Done** |
| **3** | Bayesian optimization (Optuna TPE) | **Done** |
| **4** | PPO (Gymnasium env + Rust step API) | **Done** |
| **5** | Scan replay backtester (microstructure signals) | **Done** |

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| No Python in hot loop | CPU-bound simulation must be native speed for 50-100 experiments/session |
| Vectorized strategies | Avoids per-bar Python→Rust callbacks; single array transfer |
| Config as single source of truth | `bt backtest --config X.yaml` — everything in one file, no CLI flag sprawl |
| `tick()` shared by batch + RL | Prevents logic duplication when adding PPO in Phase 4 |
| NumPy not Arrow for MVP | Simpler to implement; Arrow zero-copy deferred |
| leaderboard.json not SQLite | Single source of truth; SQLite adds a second |
| Single instrument, long-only | Simplifies MVP; multi-pair and shorts deferred |
| Feature gate for PyO3 | `cargo test` works without Python; `maturin develop` enables bindings |
