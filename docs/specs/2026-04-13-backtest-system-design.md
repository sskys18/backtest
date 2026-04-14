# Backtest System Design Spec

**Date:** 2026-04-13
**Status:** Draft v1
**Approach:** Modular CLI Pipeline — Hybrid Rust Core + Python Shell

---

## 1. Overview

Multi-strategy backtesting framework for crypto trading. Combines:

- **Rust core engine** — owns the entire simulation loop (bar iteration, order matching, portfolio tracking)
- **Python shell** — CLI, data ingestion, vectorized strategy signals, ML (TimesFM, PPO), experiment loop
- **Claude Code** — autonomous experiment iteration (AutoResearch-inspired)

Design principle: **Python prepares, Rust executes, Python analyzes.** No Python in the hot loop. Strategies are vectorized — they precompute a signal column for all bars, then Rust runs the simulation at native speed.

---

## 2. Architecture

```
┌──────────────────────────────────────────────┐
│  Claude Code experiment loop                 │  ← /loop mode, reads goals.md
│  (modifies YAML configs, runs bt CLI)        │     parses metrics.json, keeps/reverts
├──────────────────────────────────────────────┤
│  Python CLI + orchestration                  │  ← typer, YAML+Pydantic configs
│  (bt data, bt features, bt backtest, bt report)
├──────────────────────────────────────────────┤
│  Python strategy layer (vectorized)          │  ← Polars: compute signal columns
│  (indicators → strength + target_size)       │     from feature DataFrames
├──────────────────────────────────────────────┤
│  Rust core engine (PyO3 + NumPy arrays)      │  ← receives f64[] arrays once
│  (bar loop, broker, fills, portfolio, equity)│     returns equity curve + fills + metrics
└──────────────────────────────────────────────┘
```

### Data transfer: Python → Rust

Python passes **NumPy arrays** (from Polars `.to_numpy()`) to Rust via PyO3's `numpy` crate. For contiguous, null-free f64 columns this is zero-copy. Rust receives the full bar series + precomputed signal series and runs the entire simulation without calling back into Python.

Note: true Arrow zero-copy (via Arrow C Data Interface) is a future optimization. MVP uses NumPy buffer protocol which is simpler to implement and already zero-copy for contiguous f64 arrays.

### Why hybrid?

- Backtesting is CPU-bound. Rust is 10-100x faster iterating millions of bars.
- Claude Code runs 50-100 experiments per session. Sub-second backtests enable rapid iteration.
- ML libs (TimesFM, PPO) are Python-only. No Rust bindings.
- PyO3/maturin + NumPy is a proven, well-documented pattern.

---

## 3. Project Structure

```
backtest/
├── pyproject.toml                    # uv + maturin build, CLI entry "bt"
├── Cargo.toml                        # Workspace root
│
├── crates/
│   └── bt-engine/                    # Rust: the simulation engine
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs                # Crate root, re-exports
│           ├── engine.rs             # Core loop: bars[] + signals[] → results
│           ├── broker.rs             # Order matching, slippage, fees, pending orders
│           ├── portfolio.rs          # Positions, PnL, equity curve
│           ├── risk.rs               # Risk limits: position size caps, drawdown halt, signal gating
│           ├── tick.rs               # Single-bar state transition (shared by batch run + future step API)
│           ├── types.rs              # Bar, Signal, Order, Fill, Position, PortfolioState
│           └── python.rs             # PyO3 bindings: accepts NumPy arrays, returns dicts/arrays
│
├── src/
│   └── bt/
│       ├── __init__.py
│       ├── cli.py                    # Typer CLI dispatcher
│       ├── config.py                 # YAML config loader + Pydantic validation
│       │
│       ├── data/
│       │   ├── __init__.py
│       │   ├── fetch.py              # bt data fetch — multi-type fetching
│       │   ├── import_.py            # bt data import — CSV/Parquet ingestion
│       │   ├── store.py              # Normalize → Parquet per type
│       │   ├── validate.py           # bt data validate — per-type schema checks
│       │   ├── schemas.py            # Canonical schemas for each data type (Pydantic)
│       │   └── sources/
│       │       ├── base.py           # DataSource protocol (supports --type param)
│       │       ├── binance.py        # ohlcv, funding, oi, depth, trades, liquidations
│       │       ├── bithumb.py        # ohlcv (cross-exchange)
│       │       ├── upbit.py          # ohlcv (cross-exchange)
│       │       └── fx.py             # FX rate sources
│       │
│       ├── features/
│       │   ├── __init__.py
│       │   ├── build.py              # bt features build — join sources + compute indicators
│       │   ├── join.py               # Multi-source join logic (left join, resample, aggregate)
│       │   ├── registry.py           # Indicator registry (name → function)
│       │   └── indicators.py         # SMA, EMA, RSI, VWAP, Bollinger, premium, etc.
│       │
│       ├── strategy/
│       │   ├── __init__.py
│       │   ├── base.py               # Strategy protocol: compute(df) → df with signal column
│       │   ├── registry.py           # Strategy registry (name → class)
│       │   ├── momentum.py           # Trend-following / crossover
│       │   └── mean_revert.py        # Mean reversion (Bollinger, RSI)
│       │
│       ├── optimize/
│       │   ├── __init__.py
│       │   ├── run.py                # bt optimize — dispatch to optimizer
│       │   ├── base.py               # Optimizer protocol
│       │   └── grid.py               # Grid/random search (MVP)
│       │
│       ├── report/
│       │   ├── __init__.py
│       │   ├── generate.py           # bt report — JSON metrics + optional HTML
│       │   └── metrics.py            # Sharpe, Sortino, max DD, win rate, profit factor
│       │
│       └── experiment/
│           ├── __init__.py
│           ├── leaderboard.py        # Atomic read/write of leaderboard.json
│           └── manifest.py           # Run manifest: data hash, config, versions, seed
│
├── configs/
│   ├── default.yaml                  # Default backtest settings
│   └── strategies/
│       └── momentum_v1.yaml
│
├── data/                             # Parquet data store (gitignored)
│   ├── raw/                          # Per-source, per-pair, per-type: data/raw/{source}/{pair}/{type}/
│   ├── records/                      # Trading records (imported)
│   └── features/                     # Joined + computed feature DataFrames
│
├── experiments/
│   ├── goals.md                      # Claude Code optimization targets
│   ├── leaderboard.json              # Best results tracker
│   └── runs/                         # Per-run artifacts
│       └── {run_id}/
│           ├── manifest.json         # Reproducibility: data hash, config, versions, seed
│           ├── config.yaml           # Frozen input config
│           ├── metrics.json          # Sharpe, DD, returns, trade count
│           └── equity_curve.parquet  # Timestamped equity series
│
├── tests/
│   └── python/
│       ├── test_data_validate.py
│       ├── test_features.py
│       ├── test_strategy_signals.py
│       ├── test_engine_integration.py
│       └── test_report.py
│
└── docs/
    └── specs/
```

Rust tests live in `crates/bt-engine/src/` as `#[cfg(test)]` modules (unit tests) plus `crates/bt-engine/tests/` for integration tests. Run via `cargo test -p bt-engine`. Key test coverage: `tick()` state transitions, broker fill/slippage math, portfolio PnL calculations, risk halt behavior, edge cases from section 6.5.

**Key structural decisions:** No `forecast/` or `ml_signal.py` in MVP. No `arb.py` (requires multi-instrument). Includes `data/validate.py`, `risk.rs`, `tick.rs` (shared state transition), `experiment/manifest.py`. Optimize is grid-only.

---

## 4. Data Model

The system supports **multiple data types**, not just OHLCV candles. All data is stored as Parquet, organized by type. The feature builder joins everything into a single wide DataFrame by timestamp for strategies to consume.

### 4.1 Data types and schemas

Each data type has its own canonical schema. All share `timestamp` (i64, Unix ms, UTC) as the join key.

**OHLCV (required — the price backbone)**
```
timestamp   i64     Unix ms, monotonically increasing
open        f64     Opening price
high        f64     High (>= open, close)
low         f64     Low (<= open, close)
close       f64     Closing price
volume      f64     Base asset volume (>= 0)
```

**Funding rate (perpetual futures)**
```
timestamp       i64     Funding interval start
funding_rate    f64     Rate per interval (e.g., 0.0001 = 1 bps)
mark_price      f64     Mark price at funding time
```

**Open interest**
```
timestamp       i64
open_interest   f64     Total open contracts (in base asset)
```

**Order book snapshots (depth)**
```
timestamp       i64
bid_price_1..N  f64     Top N bid prices
bid_qty_1..N    f64     Top N bid quantities
ask_price_1..N  f64     Top N ask prices
ask_qty_1..N    f64     Top N ask quantities
spread          f64     ask_1 - bid_1
mid_price       f64     (ask_1 + bid_1) / 2
```

**Tick/trade data (aggregated to bar timeframe)**
```
timestamp       i64
num_trades      u64     Number of trades in bar
buy_volume      f64     Taker buy volume
sell_volume      f64     Taker sell volume
vwap            f64     Volume-weighted average price
```

**Liquidations**
```
timestamp       i64
long_liq_usd    f64     Long liquidation volume (USD)
short_liq_usd   f64     Short liquidation volume (USD)
```

**Cross-exchange prices (for arb/premium strategies)**
```
timestamp       i64
exchange        str     Exchange name (e.g., "bithumb")
close           f64     Close price on that exchange
volume          f64     Volume on that exchange
```

**FX rates**
```
timestamp       i64
pair            str     e.g., "KRW/USD"
rate            f64     Exchange rate
```

**Trading records (your own trade history)**
```
timestamp       i64     Trade execution time
side            str     "buy" or "sell"
price           f64     Execution price
quantity        f64     Base asset quantity
fee             f64     Fee paid
pnl             f64     Realized PnL (if available)
source          str     e.g., "kimchi-arbitrage", "manual"
```

### 4.2 Storage layout

```
data/
├── raw/
│   ├── binance/
│   │   └── BTC-USDT/
│   │       ├── ohlcv/1h/              # OHLCV candles
│   │       ├── funding/               # Funding rates
│   │       ├── oi/                    # Open interest
│   │       ├── depth/1h/             # Order book snapshots
│   │       ├── trades/1h/            # Aggregated tick data
│   │       └── liquidations/1h/      # Liquidation data
│   ├── bithumb/
│   │   └── BTC-KRW/
│   │       └── ohlcv/1h/             # Cross-exchange prices
│   └── fx/
│       └── KRW-USD/                   # FX rates
├── records/
│   └── my-trades-2024.parquet         # Your own trading records
└── features/
    └── BTC-USDT-1h-full.parquet       # Joined wide DataFrame
```

### 4.3 Validation rules (`bt data validate`)

Shared rules (all data types):
- Timestamps are monotonically increasing
- No duplicate timestamps
- No NaN/null values in numeric columns

OHLCV-specific:
- OHLC relationship: low <= open,close <= high
- Volume >= 0
- No gaps exceeding 3x the expected timeframe interval (gaps logged as warnings)

Validation runs automatically after `bt data fetch` and before `bt backtest`.

### 4.4 Feature config schema (`features.yaml`)

The feature builder joins multiple data sources by timestamp and computes indicators on the joined DataFrame.

```yaml
# configs/features.yaml
output: "data/features/BTC-USDT-1h-full.parquet"
timeframe: "1h"

# Data sources — joined by timestamp (left join on OHLCV timestamps)
sources:
  - type: ohlcv                                         # Required — the backbone
    path: "data/raw/binance/BTC-USDT/ohlcv/1h"

  - type: funding
    path: "data/raw/binance/BTC-USDT/funding"
    resample: "forward_fill"                             # Resample to match OHLCV timeframe

  - type: oi
    path: "data/raw/binance/BTC-USDT/oi"
    resample: "forward_fill"

  - type: trades
    path: "data/raw/binance/BTC-USDT/trades/1h"

  - type: depth
    path: "data/raw/binance/BTC-USDT/depth/1h"

  - type: cross_exchange
    path: "data/raw/bithumb/BTC-KRW/ohlcv/1h"
    prefix: "bithumb_"                                   # Columns become bithumb_close, bithumb_volume

  - type: fx
    path: "data/raw/fx/KRW-USD"
    resample: "forward_fill"

  - type: records
    path: "data/records/my-trades-2024.parquet"
    aggregate: "1h"                                      # Aggregate trades into hourly bins
    columns: [trade_count, net_volume, total_pnl]        # Output aggregated columns

# Computed indicators (applied to the joined DataFrame)
indicators:
  - name: sma
    input: close                                         # Which column to compute on
    params: { period: 12 }
    column: sma_12

  - name: sma
    input: close
    params: { period: 26 }
    column: sma_26

  - name: rsi
    input: close
    params: { period: 14 }
    column: rsi_14

  - name: bollinger
    input: close
    params: { period: 20, num_std: 2.0 }
    columns: [bb_upper, bb_middle, bb_lower]

  - name: premium                                        # Custom: kimchi premium
    input: [close, bithumb_close, fx_rate]               # Multi-input indicator
    params: {}
    column: kimchi_premium

  - name: funding_ma
    input: funding_rate
    params: { period: 24 }
    column: funding_rate_ma_24
```

**Join rules:**
- OHLCV is the backbone — its timestamps define the output rows
- All other sources are **left-joined** onto OHLCV timestamps
- Sources with different frequencies use `resample` strategy: `forward_fill` (carry last known value) or `aggregate` (sum/mean over the bar window)
- Missing data after join is filled with `null` — strategies handle nulls via `fill_null()`
- Trading records are aggregated into bar-sized bins (count, net volume, total PnL per bar)

**The output feature DataFrame** contains OHLCV columns + all joined source columns + all computed indicator columns. This single wide Parquet file is what `bt backtest` reads. The Rust engine still only needs `bars[] + signals[]` — all the data richness lives in the Python strategy layer.

Indicator `name` must match a registered function in `bt/features/registry.py`. The `input` field specifies which column(s) the indicator operates on (default: `close`).

---

## 5. Data Flow

```
[Exchange APIs / CSV files / Trading logs]
        │
        ├── bt data fetch --pair BTC-USDT --source binance --type ohlcv --timeframe 1h --start 2024-01-01
        ├── bt data fetch --pair BTC-USDT --source binance --type funding
        ├── bt data fetch --pair BTC-USDT --source binance --type oi
        ├── bt data fetch --pair BTC-KRW --source bithumb --type ohlcv --timeframe 1h
        ├── bt data fetch --pair KRW-USD --source fx
        └── bt data import --path exports/my-trades.csv --type records    # Import trading records
        │
        ▼
   bt data validate --path data/raw/binance/BTC-USDT/
        │
        ▼
   data/raw/.../*.parquet  (per-type canonical schemas)
        │
        ▼
   bt features build --config configs/features.yaml
        │  (joins all sources by timestamp, computes indicators)
        ▼
   data/features/BTC-USDT-1h-full.parquet  (wide DataFrame: OHLCV + all sources + indicators)
        │
        ▼
   bt backtest --config configs/strategies/momentum_v1.yaml
        │                                  
        │  1. Load config (Pydantic validation)
        │  2. Load features.parquet from config.backtest.data_path
        │  3. Validate: check feature df has columns strategy needs (fail fast)
        │  4. Instantiate strategy from config.strategy.name + params
        │  5. strategy.compute(df) → signals DataFrame
        │  6. Validate signals: replace NaN/null with 0.0, warn if any found
        │  7. Python → Rust: pass bars[] + signals[] as NumPy arrays
        │  8. Rust engine.run() → BacktestResult
        │  9. Save manifest.json, config.yaml, metrics.json, equity_curve.parquet
        │  10. Print metrics JSON to stdout
        │
        ▼
   experiments/runs/{run_id}/
   ├── manifest.json        (data hash, config snapshot, versions, seed)
   ├── config.yaml          (frozen input)
   ├── metrics.json         (Sharpe, max DD, total return, num trades, ...)
   └── equity_curve.parquet
        │
        ▼
   bt report --run-id {run_id}  →  stdout JSON + optional HTML
```

---

## 6. Rust Core Engine

### 6.1 Responsibility boundary

**Rust owns the entire simulation loop.** Python never calls into Rust per-bar.

| Rust (bt-engine) | Python (bt/) |
|---|---|
| Iterates all bars | Loads Parquet data |
| Consumes precomputed signal array | Computes signal array (vectorized strategy) |
| Converts signals → orders | Feature engineering (Polars) |
| Matches orders (with pending state) | Config parsing, CLI |
| Applies slippage + fees | Report generation |
| Tracks positions + portfolio | Experiment management |
| Enforces risk limits | Optimization dispatch |
| Computes equity curve | ML inference (future phases) |
| Returns results as NumPy arrays + dicts | |

### 6.2 Key types (Rust)

```rust
/// OHLCV bar — matches canonical data schema (section 4)
pub struct Bar {
    pub timestamp: i64,     // Unix ms, UTC
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

/// Order direction. MVP is long-only so only Buy (open long) and Sell (close long).
pub enum OrderSide { Buy, Sell }

/// Precomputed by Python strategy. One per bar.
/// Semantics:
///   strength >= threshold → want to be long (target_size of equity)
///   strength < threshold  → want to be flat (close any open position)
/// Both fields must be finite f64 in [0.0, 1.0]. Values outside this range
/// are clamped by the engine before processing (not panicked).
pub struct Signal {
    pub strength: f64,      // [0.0, 1.0] — 0 = no conviction, 1 = max conviction
    pub target_size: f64,   // [0.0, 1.0] — fraction of equity to allocate
}

/// MVP: market orders only. Limit orders deferred.
pub struct Order {
    pub id: u64,
    pub side: OrderSide,
    pub quantity: f64,      // Base asset quantity
    pub created_at: i64,    // Bar timestamp when created
}

pub struct Fill {
    pub order_id: u64,
    pub side: OrderSide,
    pub price: f64,         // After slippage
    pub quantity: f64,
    pub fee: f64,           // In quote currency
    pub timestamp: i64,
}

pub struct Position {
    pub entry_price: f64,   // Volume-weighted average entry
    pub quantity: f64,       // Base asset quantity (always > 0 when Some)
    pub entry_time: i64,
    pub unrealized_pnl: f64,
    // realized_pnl tracked on PortfolioState.total_pnl, not per-position (MVP: full open/close only)
}

pub struct PortfolioState {
    pub equity: f64,         // cash + position market value
    pub cash: f64,
    pub position: Option<Position>,  // MVP: single position (long or none)
    pub peak_equity: f64,            // Running peak for drawdown calculation
    pub total_pnl: f64,
    pub halted: bool,                // True if drawdown halt triggered
}

/// Returned to Python after full simulation
pub struct BacktestResult {
    pub equity_curve: Vec<(i64, f64)>,  // (timestamp, equity) per bar
    pub fills: Vec<Fill>,
    pub final_state: PortfolioState,
    pub metrics: ResultMetrics,
    pub halted_at: Option<i64>,         // Timestamp where halt triggered, if any
}

/// Computed by Rust engine. Definitions:
pub struct ResultMetrics {
    pub total_return: f64,          // (final_equity - initial) / initial
    pub sharpe_ratio: f64,          // Annualized: mean(bar_returns) / std(bar_returns) * sqrt(bars_per_year)
    pub sortino_ratio: f64,         // Like Sharpe but only downside deviation
    pub max_drawdown: f64,          // Max (peak - trough) / peak, as fraction [0, 1]
    pub num_round_trips: u32,       // Count of complete open→close trade cycles
    pub win_rate: f64,              // Fraction of round trips with positive PnL
    pub profit_factor: f64,         // Sum(winning PnL) / abs(Sum(losing PnL))
    pub bars_per_year: f64,         // Used for annualization (e.g., 8760 for 1h bars)
}
```

### Metric definitions

- **bars_per_year:** Inferred from the config timeframe. `1m` = 525600, `5m` = 105120, `15m` = 35040, `1h` = 8760, `4h` = 2190, `1d` = 365. Stored in `ResultMetrics` for transparency.
- **Sharpe ratio:** `mean(bar_returns) / std(bar_returns) * sqrt(bars_per_year)`. Bar returns = `(equity[i] - equity[i-1]) / equity[i-1]`. Risk-free rate assumed 0 for crypto. If `std(bar_returns) == 0`, Sharpe = 0.0.
- **Sortino ratio:** `mean(bar_returns) / downside_deviation * sqrt(bars_per_year)`. Downside deviation = `sqrt(mean(min(bar_returns, 0)^2))` — the square root of the second lower partial moment with target 0, using total bar count N as denominator (not just negative return count). If downside deviation == 0, Sortino = 0.0.
- **Max drawdown:** `max over all i of (peak_equity_up_to_i - equity[i]) / peak_equity_up_to_i`.
- **num_round_trips:** A round trip = one Buy fill followed by one Sell fill. Partial fills count as one trip.
- **win_rate:** `winning_round_trips / num_round_trips`. If `num_round_trips == 0`, win_rate = 0.0.
- **profit_factor:** `sum(winning_pnl) / abs(sum(losing_pnl))`. If no losing trades, profit_factor = f64::INFINITY. If no trades at all, profit_factor = 0.0.
- **Fees:** Included in all PnL and return calculations. Fill price includes slippage; fee is deducted from cash separately.

### 6.3 Engine — batch API (no per-bar callbacks)

```rust
pub struct EngineConfig {
    pub initial_capital: f64,
    pub broker: BrokerConfig,
    pub risk: RiskConfig,
}

pub struct BacktestEngine {
    broker: Broker,
    portfolio: Portfolio,
    risk: RiskManager,
    config: EngineConfig,
}

impl BacktestEngine {
    pub fn new(config: EngineConfig) -> Self { ... }

    /// Main entry point. Takes the FULL bar series and signal series.
    /// Rust owns the iteration. No Python callbacks.
    /// Returns Err if inputs are invalid (different lengths, empty, etc.)
    pub fn run(&mut self, bars: &[Bar], signals: &[Signal]) -> Result<BacktestResult, EngineError> {
        if bars.len() != signals.len() {
            return Err(EngineError::LengthMismatch { bars: bars.len(), signals: signals.len() });
        }
        if bars.is_empty() {
            return Err(EngineError::EmptyInput);
        }

        for (bar, signal) in bars.iter().zip(signals.iter()) {
            self.tick(bar, signal);
        }

        Ok(self.build_result())
    }

    /// Single-bar state transition. Shared between batch run() and
    /// future step() API for RL training (Phase 4).
    fn tick(&mut self, bar: &Bar, signal: &Signal) {
        // Clamp signal values to valid range (don't panic on bad input)
        let signal = Signal {
            strength: signal.strength.clamp(0.0, 1.0),
            target_size: signal.target_size.clamp(0.0, 1.0),
        };

        // 1. Process any pending orders from previous bar (fill at bar open + slippage)
        let pending_fills = self.broker.fill_pending(bar);
        self.portfolio.apply_fills(&pending_fills);

        // 2. Mark to market FIRST — so drawdown check sees current-bar state
        self.portfolio.mark_to_market(bar);

        // 3. Risk check: if halted, stop accepting new orders but keep marking to market
        if self.risk.should_halt(&self.portfolio) {
            // If holding a position, close it (market sell at bar close)
            if self.portfolio.has_position() {
                let close_order = self.risk.build_emergency_close(&self.portfolio, bar);
                let fill = self.broker.fill_immediate(&close_order, bar);
                self.portfolio.apply_fills(&[fill]);
                self.portfolio.mark_to_market(bar);  // Re-mark after liquidation
            }
            self.portfolio.state.halted = true;
            self.record_equity(bar.timestamp);
            return;  // Continue iterating bars (for equity curve) but no new orders
        }

        // 4. Already halted from prior bar — just record equity, no trading
        if self.portfolio.state.halted {
            self.record_equity(bar.timestamp);
            return;
        }

        // 5. Convert signal to order (risk manager may reject/resize)
        //    - strength >= threshold → want to be long (Buy if flat, resize if already long)
        //    - strength < threshold  → want to be flat (Sell to close if holding)
        //    - If already in desired state with same size → no order
        if let Some(order) = self.risk.gate_signal(&signal, &self.portfolio, bar) {
            match self.config.broker.fill_model {
                FillModel::Immediate => {
                    let fill = self.broker.fill_immediate(&order, bar);
                    self.portfolio.apply_fills(&[fill]);
                    self.portfolio.mark_to_market(bar);  // Re-mark so equity reflects the fill
                }
                FillModel::NextBar => {
                    // Cancel any existing pending order before enqueuing new one
                    self.broker.cancel_pending();
                    self.broker.enqueue_pending(order);
                }
            }
        }

        // 6. Record equity snapshot
        self.record_equity(bar.timestamp);
    }
}
```

**Key design: `tick()` is the shared state transition.** Both the batch `run()` loop and the future Phase 4 `step(action)` API call `tick()`. This prevents logic duplication when adding RL support later.

### Portfolio method contracts

**`apply_fills(fills: &[Fill])`** — processes fills in order:
- **Buy fill:** Deduct `fill.price * fill.quantity + fill.fee` from `cash`. If no position, create one with `entry_price = fill.price`. If position exists (shouldn't happen in MVP), average into entry price. If cash would go negative, clamp `fill.quantity` to max affordable.
- **Sell fill:** Add `fill.price * fill.quantity - fill.fee` to `cash`. Compute `realized_pnl = (fill.price - position.entry_price) * fill.quantity - fill.fee - buy_fee`. Add to `PortfolioState.total_pnl`. Set `position = None`. If no position exists, ignore the fill (defensive).

**`mark_to_market(bar: &Bar)`** — updates equity from current prices:
1. If position exists: `unrealized_pnl = (bar.close - entry_price) * quantity`
2. `equity = cash + (position quantity * bar.close)` (or just `cash` if no position)
3. `peak_equity = max(peak_equity, equity)`
4. `current_drawdown = (peak_equity - equity) / peak_equity`

**`has_position() → bool`** — returns `self.position.is_some()`
**`position_quantity() → f64`** — returns position quantity (panics if no position — caller must check `has_position()` first)

### 6.4 Broker model

```rust
pub struct BrokerConfig {
    pub slippage_bps: f64,        // e.g., 5.0 = 5 basis points
    pub fee_rate: f64,            // e.g., 0.001 = 10 bps (taker fee; all MVP fills are taker)
    pub fill_model: FillModel,
}

pub enum FillModel {
    /// Fill at current bar close + slippage. Simplest, less realistic.
    Immediate,
    /// Enqueue order, fill at next bar open + slippage. More realistic.
    /// Recommended default for backtesting.
    NextBar,
}

// VolumeWeighted and Limit orders deferred to post-MVP.
```

**Slippage model:** `fill_price = base_price * (1 + slippage_bps / 10000)` for Buy, `* (1 - slippage_bps / 10000)` for Sell. Base price = bar close (Immediate) or bar open (NextBar).

**Fee model:** `fee = fill_price * quantity * fee_rate`. Deducted from cash. All fills are taker in MVP.

**Order of operations per bar (deterministic, matches `tick()`):**
1. Fill pending orders (from previous bar) at current bar's **open** + slippage
2. Mark to market at current bar's close (so drawdown sees current state)
3. Check risk limits — if drawdown exceeded: liquidate position, halt, record equity, stop
4. If already halted: record equity, stop
5. Evaluate signal → generate new order (risk manager may reject/resize)
6. If Immediate: fill at current bar's **close** + slippage
7. If NextBar: cancel any existing pending, enqueue new pending
8. Record equity snapshot

### 6.5 Risk enforcement (Rust)

```rust
pub struct RiskConfig {
    pub max_position_size: f64,     // Max fraction of equity per position (e.g., 0.2 = 20%)
    pub max_drawdown_halt: f64,     // Halt trading when drawdown >= this (e.g., 0.15 = 15%)
    pub signal_threshold: f64,      // Min signal strength to go/stay long (e.g., 0.5)
}

impl RiskManager {
    /// Signal gating logic:
    ///   strength >= threshold → want to be long (open or hold)
    ///   strength <  threshold → want to be flat (close if holding)
    ///
    /// Returns:
    ///   Some(Buy order)  — if want long AND currently flat
    ///   Some(Sell order) — if want flat AND currently holding
    ///   None             — if already in desired state
    pub fn gate_signal(&self, signal: &Signal, portfolio: &Portfolio, bar: &Bar) -> Option<Order> {
        let want_long = signal.strength >= self.config.signal_threshold;
        let is_holding = portfolio.has_position();

        if want_long && !is_holding {
            // Enter: cap position size, build Buy order
            let capped_size = signal.target_size.min(self.config.max_position_size);
            // Approximate quantity. Slippage + fees may push actual cost slightly higher.
            // apply_fills() guards against negative cash by clamping fill quantity.
            let qty = (portfolio.state.equity * capped_size) / bar.close;
            Some(Order { id: self.next_id(), side: OrderSide::Buy, quantity: qty, created_at: bar.timestamp })
        } else if !want_long && is_holding {
            // Exit: close entire position
            let qty = portfolio.position_quantity();
            Some(Order { id: self.next_id(), side: OrderSide::Sell, quantity: qty, created_at: bar.timestamp })
        } else {
            // Already in desired state (long and want long, or flat and want flat)
            // MVP: no position resizing while holding. Deferred.
            None
        }
    }

    pub fn should_halt(&self, portfolio: &Portfolio) -> bool {
        portfolio.current_drawdown() >= self.config.max_drawdown_halt
    }
}
```

**Edge cases:**
- **Duplicate long signal while already long:** No new order (hold existing position).
- **Signal flip to flat while pending Buy exists:** `tick()` calls `broker.cancel_pending()` before enqueuing, so the pending Buy is cancelled and a Sell is submitted instead.
- **Drawdown halt:** Liquidates open position via `fill_immediate` at bar close, sets `halted = true`. This is an **emergency liquidation** — it always uses Immediate fill regardless of the configured `fill_model` (intentional: emergency close shouldn't wait a bar). Remaining bars still get equity snapshots (marking to market) but no new orders.
- **Final bar with NextBar pending:** Pending order is never filled. `BacktestResult.fills` will not include it. This is correct — unfilled orders expire.
- **Fewer bars than warmup:** Engine runs normally. Signals will be 0.0 (from null/NaN → 0.0 clamping), so no trades. Valid backtest with zero round trips.

### 6.6 PyO3 boundary

```python
# Python side — NumPy arrays via PyO3 numpy crate
import polars as pl
from bt_engine import BacktestEngine, EngineConfig

# Load data
features_df = pl.read_parquet("data/features/BTC-USDT-1h.parquet")

# Strategy computes signals (vectorized, pure Polars)
signals_df = strategy.compute(features_df)
# signals_df has columns: strength (f64), target_size (f64)

# Create engine — config mirrors Rust EngineConfig nesting
config = EngineConfig(
    initial_capital=10_000.0,
    broker={
        "slippage_bps": 5.0,
        "fee_rate": 0.001,
        "fill_model": "next_bar",
    },
    risk={
        "max_position_size": 0.2,
        "max_drawdown_halt": 0.15,
        "signal_threshold": 0.5,
    },
)
engine = BacktestEngine(config)

# Run — passes NumPy arrays (zero-copy for contiguous f64), Rust iterates
result = engine.run(
    timestamps=features_df["timestamp"].to_numpy(),
    opens=features_df["open"].to_numpy(),
    highs=features_df["high"].to_numpy(),
    lows=features_df["low"].to_numpy(),
    closes=features_df["close"].to_numpy(),
    volumes=features_df["volume"].to_numpy(),
    strengths=signals_df["strength"].to_numpy(),
    target_sizes=signals_df["target_size"].to_numpy(),
)

# result["equity_curve"]   → numpy array of shape (N, 2): [[timestamp, equity], ...]
# result["fills"]          → list of dicts [{order_id, side, price, quantity, fee, timestamp}, ...]
# result["metrics"]        → dict {total_return, sharpe_ratio, sortino_ratio, max_drawdown, ...}
# result["halted_at"]      → Optional[int] — timestamp where halt triggered, or None
# result["final_state"]    → dict {equity, cash, position, peak_equity, total_pnl, halted}
```

**PyO3 implementation notes:**
- Use `pyo3` crate with `numpy` feature for array access
- Rust reads NumPy buffers as `PyReadonlyArray1<f64>` — zero-copy for contiguous arrays
- Results returned as Python dict + NumPy arrays (equity curve) via `IntoPyObject`
- `EngineConfig` accepts Python dict, validated in Rust (not Pydantic — keep boundary simple)
- Errors returned as Python `ValueError`, not panics

---

## 7. Python Layer

### 7.1 Strategy protocol (vectorized)

```python
class Strategy(Protocol):
    """Strategies are vectorized. They receive the full feature DataFrame
    and return a DataFrame with signal columns. No per-bar callbacks."""

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        """Input: features DataFrame (OHLCV + indicators).
        Output: DataFrame with 'strength' (f64) and 'target_size' (f64) columns.
        Must have same row count as input. Must not look ahead."""
        ...
```

Example momentum strategy:

```python
class MomentumStrategy:
    def __init__(self, fast_period: int = 12, slow_period: int = 26,
                 position_size: float = 0.15):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_size = position_size

    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        fast_ma = df["close"].rolling_mean(self.fast_period)
        slow_ma = df["close"].rolling_mean(self.slow_period)

        # Signal: 1.0 when fast > slow, 0.0 otherwise
        # .fill_null(0.0) handles warmup period where rolling_mean returns null
        strength = (fast_ma > slow_ma).cast(pl.Float64).fill_null(0.0)

        return pl.DataFrame({
            "strength": strength,
            "target_size": pl.Series("target_size", [self.position_size] * len(df)),
        })
```

**Warmup and null handling:**
- Indicators produce null for the first N bars (warmup period). Strategies MUST replace nulls with 0.0 (meaning "no conviction" → go flat).
- Strategies must NOT use forward/backward fill on signals (lookahead risk). Only `fill_null(0.0)` is safe.
- The Python `bt backtest` driver validates signals before passing to Rust: any remaining NaN/null values are replaced with 0.0 and a warning is logged.
- Rust engine clamps all signal values to [0.0, 1.0]. Out-of-range values are clamped, not rejected.

### 7.2 Other protocols

```python
class DataSource(Protocol):
    """Any data provider. Supports multiple data types per source."""
    def supported_types(self) -> list[str]:
        """Returns list of supported data types (e.g., ['ohlcv', 'funding', 'oi', 'trades'])"""
        ...

    async def fetch(
        self, pair: str, data_type: str, timeframe: str,
        start: datetime, end: datetime,
    ) -> pl.DataFrame:
        """Returns DataFrame matching the canonical schema for the given data_type.
        Raises ValueError if data_type not in supported_types()."""
        ...

class Optimizer(Protocol):
    """Runs multiple backtests with different configs."""
    def optimize(
        self, objective: Callable[[dict], float],
        search_space: dict, budget: int,
    ) -> dict:
        """Returns {best_config, best_score, history: [{config, score}]}"""
        ...

# Search space format for grid/random search:
# Keys are YAML config paths. Values define the search range.
# {
#     "strategy.params.fast_period": {"min": 5, "max": 50, "step": 5},       # integer range
#     "strategy.params.slow_period": {"min": 10, "max": 200, "step": 10},    # integer range
#     "strategy.params.position_size": {"values": [0.05, 0.10, 0.15, 0.20]}, # explicit list
#     "risk.signal_threshold": {"min": 0.3, "max": 0.8, "step": 0.1},       # float range
# }
# Grid search: cartesian product of all ranges. Random search: sample uniformly.
```

### 7.3 CLI commands

All commands return **JSON to stdout** on success (exit 0). Errors return non-zero + error JSON.

| Command | Input | Output | Purpose |
|---|---|---|---|
| `bt data fetch` | --pair, --source, --type, --timeframe, --start, --end | `data/raw/.../*.parquet` | Pull market data (ohlcv, funding, oi, depth, trades, liquidations) |
| `bt data import` | --path, --type | `data/raw/.../*.parquet` or `data/records/` | Import CSV/Parquet (trading records, FX rates, custom data) |
| `bt data validate` | --path | JSON validation report | Check data quality |
| `bt data list` | — | JSON list of all datasets by type | Inventory |
| `bt features build` | --config | `data/features/*.parquet` | Join sources + compute indicators |
| `bt backtest` | --config | `experiments/runs/{id}/` | Run backtest (config is single source of truth) |
| `bt report` | --run-id | JSON metrics + optional HTML | Generate report |
| `bt optimize` | --config, --method, --space, --budget | best config + history | Parameter search |
| `bt strategy list` | — | JSON list of registered strategies | Discovery |

### 7.4 Config schema (YAML + Pydantic)

```yaml
# configs/strategies/momentum_v1.yaml
strategy:
  name: momentum              # Must match a registered strategy name in bt/strategy/registry.py
  params:
    fast_period: 12
    slow_period: 26
    position_size: 0.15

backtest:
  pair: "BTC-USDT"            # Single instrument per backtest (MVP)
  timeframe: "1h"
  start: "2024-01-01"
  end: "2025-01-01"
  initial_capital: 10000.0
  data_path: "data/features/BTC-USDT-1h.parquet"  # Explicit path to feature data
  # bars_per_year is inferred from timeframe (1h → 8760). Used for Sharpe annualization.

broker:
  slippage_bps: 5.0
  fee_rate: 0.001             # 10 bps per fill (all taker in MVP)
  fill_model: "next_bar"      # "immediate" or "next_bar"

risk:
  max_position_size: 0.20     # Enforced by Rust RiskManager — caps order size
  max_drawdown_halt: 0.15     # Enforced by Rust RiskManager — liquidates + halts
  signal_threshold: 0.5       # Enforced by Rust RiskManager — min strength to go long
```

**Config conventions:**
- `strategy.name` is the key used for lookup in the strategy registry.
- CLI usage: `bt backtest --config configs/strategies/momentum_v1.yaml`. The config is the single source of truth.
- **Path resolution:** All paths in config (`data_path`, etc.) are relative to the project root (directory containing `pyproject.toml`). The CLI resolves them at startup using the project root, not the current working directory.
- **Run ID generation:** Format is `YYYY-MM-DD_{NNN}` where NNN is a zero-padded sequence number. Generated by scanning `experiments/runs/` for existing directories with today's date prefix and incrementing. No concurrent writes in MVP.
- `pair` and `timeframe` are metadata for the manifest and report — they are NOT validated against `data_path` content in MVP. The developer is responsible for consistency. Post-MVP: derive `data_path` from `pair` + `timeframe` automatically.

---

## 8. Reproducibility

Every backtest run produces a `manifest.json` capturing everything needed to reproduce:

```json
{
  "run_id": "2026-04-13_001",
  "timestamp": "2026-04-13T14:30:00Z",
  "config": { "...frozen copy of input YAML..." },
  "data_path": "data/features/BTC-USDT-1h.parquet",
  "data_hash": "sha256:abc123...",
  "strategy_source_hash": "sha256:bbb222...",
  "feature_config_hash": "sha256:ccc333...",
  "bt_engine_version": "0.1.0",
  "python_version": "3.12.3",
  "rust_version": "1.82.0",
  "git_commit": "a1b2c3d",
  "uv_lock_hash": "sha256:def456..."
}
```

**Fields:**
- `data_hash` — SHA256 of the feature Parquet file
- `strategy_source_hash` — SHA256 of the strategy .py source file
- `feature_config_hash` — SHA256 of features.yaml used to build the feature file
- `git_commit` — current HEAD at time of run
- `uv_lock_hash` — SHA256 of uv.lock for dependency pinning

**Rule:** Two runs with the same manifest inputs must produce identical outputs. The Rust engine is fully deterministic (no randomness in MVP broker model). No random seed needed in MVP.

---

## 9. Claude Code Experiment Loop

Claude Code runs in `/loop` mode. It is NOT a Python script — it's the agent itself iterating.

### 9.1 goals.md contract

Parameter bounds use **full YAML config paths** so Claude Code knows exactly which field to modify.

```markdown
# Experiment Goals

## Objective
Maximize Sharpe ratio. Tie-breaker: lower max_drawdown, then higher total_return.

## Constraints
- metrics.max_drawdown < 0.15
- metrics.num_round_trips >= 50

## Allowed mutations
- Modify strategy params in YAML configs (within bounds below)
- Create new YAML config files (must reference existing registered strategies)
- DO NOT modify .py source files

## Parameter bounds (YAML config paths)
- strategy.params.fast_period: [5, 100]       # integer
- strategy.params.slow_period: [10, 200]       # integer
- strategy.params.position_size: [0.05, 0.30]  # float
- risk.signal_threshold: [0.3, 0.8]            # float
- broker.slippage_bps: [3.0, 10.0]             # float

## Fixed config (DO NOT modify)
- backtest.pair: "BTC-USDT"
- backtest.timeframe: "1h"
- backtest.data_path: "data/features/BTC-USDT-1h.parquet"
- backtest.initial_capital: 10000.0
- broker.fee_rate: 0.001
- broker.fill_model: "next_bar"

## Budget
50 experiments
```

### 9.2 Loop behavior (what Claude Code actually does)

```
1.  Read goals.md — understand objective, constraints, bounds, budget
2.  Read experiments/leaderboard.json — see past results and current best
3.  Count existing runs against budget — stop if exhausted
4.  Pick next config (guided by past results and unexplored regions)
5.  Write configs/experiments/exp_{N}.yaml (N = next sequential number)
6.  Run: bt backtest --config configs/experiments/exp_{N}.yaml
7.  If exit code != 0:
      - Record in experiments/runs/{id}/error.txt (stderr + config hash)
      - DO NOT retry the same or near-duplicate config
      - Go to step 2
8.  Parse: experiments/runs/{id}/metrics.json
9.  Check constraints from goals.md
10. If feasible AND Sharpe > leaderboard best (or tie-breaker wins):
      - Update experiments/leaderboard.json (atomic: write .tmp → rename)
11. Go to step 2 until budget exhausted
12. Write experiments/session_summary.md with findings
```

### 9.3 Guardrails

- **Allowed mutations are explicit** — goals.md specifies exact YAML paths and bounds
- **No .py file modification in MVP** — only YAML config changes (safe, reversible)
- **Every run is immutable** — once written, experiments/runs/{id}/ is never modified
- **Budget is enforced** — Claude Code counts runs in experiments/runs/ against budget
- **Atomic leaderboard updates** — write to `.tmp` file, then `os.rename()` (atomic on POSIX)
- **Failure recording** — non-zero exit saves stderr + config hash to `error.txt`, prevents near-duplicate retries
- **No concurrency in MVP** — single Claude Code loop, single backtest at a time

### 9.4 Experiment tracking

**MVP uses `leaderboard.json` only.** No SQLite — it would be a second source of truth. All run data is already in `experiments/runs/{id}/metrics.json`.

```json
// experiments/leaderboard.json
{
  "best_run_id": "2026-04-13_007",
  "best_sharpe": 1.42,
  "best_metrics": { "...full metrics from that run..." },
  "total_runs": 12,
  "feasible_runs": 8,
  "history": [
    {"run_id": "2026-04-13_001", "sharpe": 0.85, "feasible": true},
    {"run_id": "2026-04-13_002", "sharpe": -0.12, "feasible": false, "reason": "max_drawdown exceeded"}
  ]
}
```

If queryable experiment history is needed later, add SQLite as a post-MVP enhancement (single source of truth, replacing leaderboard.json).

---

## 10. Technology Stack

| Concern | Choice | Why |
|---|---|---|
| Rust engine | **PyO3 + maturin** | Proven pattern. NumPy arrays for data transfer (zero-copy for contiguous f64). |
| Data frames | **Polars** | Faster than pandas, lazy eval, Parquet-native |
| Config | **YAML + Pydantic** | Human-readable, type-validated, Claude Code can edit YAML |
| CLI | **Typer** | Auto help, type annotations, clean JSON output for Claude Code |
| Storage | **Parquet** (partitioned) | Columnar, fast, compressed, no DB needed |
| Experiment tracking | **leaderboard.json** + run dirs | Simple, atomic, single source of truth. SQLite deferred to post-MVP. |
| exchange-adapters | **uv workspace** or **pip install -e** (absolute path) | See section 11 |
| Testing | **pytest** (Python) + **cargo test** (Rust) | Standard for both |
| Build | **uv + maturin** | uv handles Python deps, maturin builds Rust → Python wheel |

---

## 11. exchange-adapters Integration

**Option A (preferred): uv workspace**

If exchange-adapters has a pyproject.toml, make both projects part of a uv workspace:

```toml
# /Users/sskys/Mine/pyproject.toml (workspace root — uv requires this file name)
[tool.uv.workspace]
members = ["backtest", "exchange-adapters"]
```

**Option B: absolute path editable install**

```toml
# backtest/pyproject.toml
[project]
dependencies = [
    "exchange-adapters",
]

[tool.uv.sources]
exchange-adapters = { path = "/Users/sskys/Mine/exchange-adapters", editable = true }
```

Used **only** in `bt/data/sources/`. The rest of the system never imports exchange-adapters.

---

## 12. Progressive ML Path

| Phase | What's added | Prerequisite |
|---|---|---|
| **1 (MVP)** | Rust engine + vectorized strategies + grid search + CLI | — |
| **2** | TimesFM forecasts as additional feature columns | Phase 1 engine correct + reproducible |
| **3** | Bayesian optimization (Optuna) | Phase 1 optimizer working |
| **4** | PPO (Gymnasium env wrapping Rust engine, step-by-step API) | Phase 1 engine + separate Rust API for per-step interaction |

**Phase 4 notes:**
- PPO requires a **different Rust API** — a step-by-step `engine.step(action) → (obs, reward, done)` that Python calls per timestep. This reuses the same `tick()` internal function as the batch API (section 6.3).
- The step API will be slower (Python in the loop) but that's inherent to RL training. The batch `run()` API remains for non-RL backtesting.
- **Signal model mismatch:** MVP signals are `(strength, target_size)` — a target-position model. PPO may want an explicit action schema (e.g., continuous `[-1, 1]` for position target). The `tick()` function accepts `Signal` internally, so the PPO adapter would translate `action → Signal` before calling `tick()`. This translation layer is Phase 4 scope.
- **Reproducibility:** Phase 4 will need additional manifest fields: model artifact hash, observation schema version, reward function definition, and random seeds (PPO is stochastic).

---

## 13. What's NOT in scope (YAGNI)

- Multi-instrument portfolios (single pair per backtest in MVP)
- Short selling / margin / leverage / liquidation (long-only + flat in MVP)
- Cross-exchange arbitrage strategies (requires multi-instrument)
- Limit orders / maker fees (market orders only in MVP, all fills are taker)
- Position resizing while holding (enter or exit only, no partial adjustments)
- Live trading engine (separate project)
- VolumeWeighted fill model (requires participation rate logic)
- SQLite experiment tracking (leaderboard.json is sufficient for MVP)
- Web dashboard (CLI + HTML reports)
- Cloud deployment (local first)
- Real-time streaming
- TimesFM / PPO (deferred to Phase 2+)
- Concurrent experiment runs (single sequential loop in MVP)
