# Plan A: Rust Engine + Project Scaffold

**Spec:** `docs/specs/2026-04-13-backtest-system-design.md`
**Scope:** Project scaffold (pyproject.toml, Cargo.toml, git), all Rust engine code (types, broker, portfolio, risk, tick, engine, metrics, PyO3 bindings)
**Depends on:** Nothing — this is the foundation
**Testable output:** `cargo test -p bt-engine` passes, `python -c "from bt_engine import BacktestEngine"` works, a simple Python script runs a backtest against hardcoded arrays and returns correct metrics

---

## Work Stream 1: Project Scaffold

### Task 1.1: Initialize git repo and directory structure

```bash
cd /Users/sskys/Mine/backtest
git init
mkdir -p crates/bt-engine/src
mkdir -p src/bt/data/sources
mkdir -p src/bt/features
mkdir -p src/bt/strategy
mkdir -p src/bt/optimize
mkdir -p src/bt/report
mkdir -p src/bt/experiment
mkdir -p configs/strategies
mkdir -p data/raw data/records data/features
mkdir -p experiments/runs
mkdir -p tests/python
mkdir -p docs/plans docs/specs
```

Create `.gitignore`:

```gitignore
# Rust
target/
*.so
*.dylib

# Python
__pycache__/
*.pyc
*.egg-info/
.venv/
dist/
build/

# Data (large files)
data/raw/
data/records/
data/features/

# Experiments (generated)
experiments/runs/
experiments/leaderboard.json

# IDE
.idea/
.vscode/
*.swp

# Environment
.env
```

**Verify:** `git status` shows clean repo with .gitignore.

### Task 1.2: Create Cargo workspace root

File: `Cargo.toml`

```toml
[workspace]
members = ["crates/bt-engine"]
resolver = "2"
```

**Verify:** `cargo check` finds the workspace (will error on missing member — that's expected, fixed in Task 1.3).

### Task 1.3: Create bt-engine crate Cargo.toml

File: `crates/bt-engine/Cargo.toml`

```toml
[package]
name = "bt-engine"
version = "0.1.0"
edition = "2021"

[lib]
name = "bt_engine"
crate-type = ["cdylib", "rlib"]

[dependencies]
pyo3 = { version = "0.23", features = ["extension-module"] }
numpy = "0.23"
thiserror = "2"

[dev-dependencies]
approx = "0.5"
```

**Verify:** `cargo check -p bt-engine` succeeds (after creating lib.rs stub in Task 2.1).

### Task 1.4: Create pyproject.toml

File: `pyproject.toml`

```toml
[build-system]
requires = ["maturin>=1.7,<2.0"]
build-backend = "maturin"

[project]
name = "bt"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "polars>=1.0",
    "typer>=0.12",
    "pyyaml>=6.0",
    "pydantic>=2.0",
    "numpy>=1.26",
]

[project.scripts]
bt = "bt.cli:app"

[tool.maturin]
python-source = "src"
module-name = "bt.bt_engine"
features = ["pyo3/extension-module"]
manifest-path = "crates/bt-engine/Cargo.toml"

[tool.pytest.ini_options]
testpaths = ["tests/python"]
```

**Verify:** File exists and is valid TOML.

### Task 1.5: Create Python package stubs

Create empty `__init__.py` files for the package structure:

- `src/bt/__init__.py` — `"""Backtest CLI — bt"""`
- `src/bt/data/__init__.py`
- `src/bt/data/sources/__init__.py`
- `src/bt/features/__init__.py`
- `src/bt/strategy/__init__.py`
- `src/bt/optimize/__init__.py`
- `src/bt/report/__init__.py`
- `src/bt/experiment/__init__.py`

**Verify:** `python -c "import bt"` works (after maturin develop in Task 5).

### Task 1.6: First commit

```bash
git add -A
git commit -m "Initialize project scaffold: Cargo workspace + pyproject.toml + directory structure"
```

---

## Work Stream 2: Rust Types (types.rs)

### Task 2.1: Create lib.rs stub

File: `crates/bt-engine/src/lib.rs`

```rust
pub mod types;

pub use types::*;
```

### Task 2.2: Implement core types

File: `crates/bt-engine/src/types.rs`

```rust
use thiserror::Error;

#[derive(Debug, Clone, Copy)]
pub struct Bar {
    pub timestamp: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy)]
pub struct Signal {
    pub strength: f64,
    pub target_size: f64,
}

#[derive(Debug, Clone)]
pub struct Order {
    pub id: u64,
    pub side: OrderSide,
    pub quantity: f64,
    pub created_at: i64,
}

#[derive(Debug, Clone)]
pub struct Fill {
    pub order_id: u64,
    pub side: OrderSide,
    pub price: f64,
    pub quantity: f64,
    pub fee: f64,
    pub timestamp: i64,
}

#[derive(Debug, Clone)]
pub struct Position {
    pub entry_price: f64,
    pub quantity: f64,
    pub entry_time: i64,
    pub unrealized_pnl: f64,
}

#[derive(Debug, Clone)]
pub struct PortfolioState {
    pub equity: f64,
    pub cash: f64,
    pub position: Option<Position>,
    pub peak_equity: f64,
    pub total_pnl: f64,
    pub halted: bool,
}

#[derive(Debug, Clone)]
pub struct BacktestResult {
    pub equity_curve: Vec<(i64, f64)>,
    pub fills: Vec<Fill>,
    pub final_state: PortfolioState,
    pub metrics: ResultMetrics,
    pub halted_at: Option<i64>,
}

#[derive(Debug, Clone)]
pub struct ResultMetrics {
    pub total_return: f64,
    pub sharpe_ratio: f64,
    pub sortino_ratio: f64,
    pub max_drawdown: f64,
    pub num_round_trips: u32,
    pub win_rate: f64,
    pub profit_factor: f64,
    pub bars_per_year: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FillModel {
    Immediate,
    NextBar,
}

#[derive(Debug, Clone)]
pub struct BrokerConfig {
    pub slippage_bps: f64,
    pub fee_rate: f64,
    pub fill_model: FillModel,
}

#[derive(Debug, Clone)]
pub struct RiskConfig {
    pub max_position_size: f64,
    pub max_drawdown_halt: f64,
    pub signal_threshold: f64,
}

#[derive(Debug, Clone)]
pub struct EngineConfig {
    pub initial_capital: f64,
    pub broker: BrokerConfig,
    pub risk: RiskConfig,
    pub bars_per_year: f64,
}

#[derive(Debug, Error)]
pub enum EngineError {
    #[error("bars length ({bars}) != signals length ({signals})")]
    LengthMismatch { bars: usize, signals: usize },
    #[error("empty input: bars and signals must have at least one element")]
    EmptyInput,
    #[error("invalid config: {0}")]
    InvalidConfig(String),
}
```

**Verify:** `cargo check -p bt-engine` succeeds.

### Task 2.3: Commit types

```bash
git add crates/bt-engine/
git commit -m "Add Rust core types: Bar, Signal, Order, Fill, Position, PortfolioState, configs"
```

---

## Work Stream 3: Broker (broker.rs)

### Task 3.1: Implement Broker

File: `crates/bt-engine/src/broker.rs`

```rust
use crate::types::*;

pub struct Broker {
    config: BrokerConfig,
    pending: Option<Order>,
}

impl Broker {
    pub fn new(config: BrokerConfig) -> Self {
        Self { config, pending: None }
    }

    /// Fill a market order immediately at bar close + slippage.
    pub fn fill_immediate(&self, order: &Order, bar: &Bar) -> Fill {
        let base_price = bar.close;
        let fill_price = self.apply_slippage(base_price, order.side);
        let fee = fill_price * order.quantity * self.config.fee_rate;

        Fill {
            order_id: order.id,
            side: order.side,
            price: fill_price,
            quantity: order.quantity,
            fee,
            timestamp: bar.timestamp,
        }
    }

    /// Fill pending orders at bar open + slippage. Returns fills and clears pending.
    pub fn fill_pending(&mut self, bar: &Bar) -> Vec<Fill> {
        let Some(order) = self.pending.take() else {
            return Vec::new();
        };

        let base_price = bar.open;
        let fill_price = self.apply_slippage(base_price, order.side);
        let fee = fill_price * order.quantity * self.config.fee_rate;

        vec![Fill {
            order_id: order.id,
            side: order.side,
            price: fill_price,
            quantity: order.quantity,
            fee,
            timestamp: bar.timestamp,
        }]
    }

    pub fn enqueue_pending(&mut self, order: Order) {
        self.pending = Some(order);
    }

    pub fn cancel_pending(&mut self) {
        self.pending = None;
    }

    fn apply_slippage(&self, base_price: f64, side: OrderSide) -> f64 {
        let slip = self.config.slippage_bps / 10_000.0;
        match side {
            OrderSide::Buy => base_price * (1.0 + slip),
            OrderSide::Sell => base_price * (1.0 - slip),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use approx::assert_relative_eq;

    fn test_config() -> BrokerConfig {
        BrokerConfig {
            slippage_bps: 5.0,
            fee_rate: 0.001,
            fill_model: FillModel::NextBar,
        }
    }

    fn test_bar() -> Bar {
        Bar {
            timestamp: 1000,
            open: 100.0,
            high: 105.0,
            low: 95.0,
            close: 102.0,
            volume: 1000.0,
        }
    }

    #[test]
    fn test_fill_immediate_buy() {
        let broker = Broker::new(test_config());
        let order = Order { id: 1, side: OrderSide::Buy, quantity: 1.0, created_at: 1000 };
        let bar = test_bar();
        let fill = broker.fill_immediate(&order, &bar);

        // close=102, slippage=5bps → 102 * 1.0005 = 102.051
        assert_relative_eq!(fill.price, 102.0 * 1.0005, epsilon = 1e-10);
        assert_relative_eq!(fill.fee, fill.price * 1.0 * 0.001, epsilon = 1e-10);
        assert_eq!(fill.side, OrderSide::Buy);
    }

    #[test]
    fn test_fill_immediate_sell() {
        let broker = Broker::new(test_config());
        let order = Order { id: 2, side: OrderSide::Sell, quantity: 0.5, created_at: 1000 };
        let bar = test_bar();
        let fill = broker.fill_immediate(&order, &bar);

        // close=102, slippage=5bps → 102 * 0.9995 = 101.949
        assert_relative_eq!(fill.price, 102.0 * 0.9995, epsilon = 1e-10);
        assert_eq!(fill.quantity, 0.5);
    }

    #[test]
    fn test_fill_pending_at_open() {
        let mut broker = Broker::new(test_config());
        let order = Order { id: 3, side: OrderSide::Buy, quantity: 2.0, created_at: 999 };
        broker.enqueue_pending(order);

        let bar = test_bar();
        let fills = broker.fill_pending(&bar);

        assert_eq!(fills.len(), 1);
        // open=100, slippage=5bps → 100 * 1.0005 = 100.05
        assert_relative_eq!(fills[0].price, 100.0 * 1.0005, epsilon = 1e-10);
    }

    #[test]
    fn test_no_pending() {
        let mut broker = Broker::new(test_config());
        let bar = test_bar();
        let fills = broker.fill_pending(&bar);
        assert!(fills.is_empty());
    }

    #[test]
    fn test_cancel_pending() {
        let mut broker = Broker::new(test_config());
        broker.enqueue_pending(Order { id: 4, side: OrderSide::Buy, quantity: 1.0, created_at: 1000 });
        broker.cancel_pending();
        let fills = broker.fill_pending(&test_bar());
        assert!(fills.is_empty());
    }
}
```

Update `lib.rs`:

```rust
pub mod types;
pub mod broker;

pub use types::*;
```

**Verify:** `cargo test -p bt-engine -- broker` — all 5 tests pass.

### Task 3.2: Commit broker

```bash
git add crates/bt-engine/src/broker.rs crates/bt-engine/src/lib.rs
git commit -m "Add Broker: fill_immediate, fill_pending, slippage model, fee calculation"
```

---

## Work Stream 4: Portfolio (portfolio.rs)

### Task 4.1: Implement Portfolio

File: `crates/bt-engine/src/portfolio.rs`

```rust
use crate::types::*;

pub struct Portfolio {
    pub state: PortfolioState,
}

impl Portfolio {
    pub fn new(initial_capital: f64) -> Self {
        Self {
            state: PortfolioState {
                equity: initial_capital,
                cash: initial_capital,
                position: None,
                peak_equity: initial_capital,
                total_pnl: 0.0,
                halted: false,
            },
        }
    }

    pub fn apply_fills(&mut self, fills: &[Fill]) {
        for fill in fills {
            match fill.side {
                OrderSide::Buy => self.apply_buy(fill),
                OrderSide::Sell => self.apply_sell(fill),
            }
        }
    }

    fn apply_buy(&mut self, fill: &Fill) {
        let cost = fill.price * fill.quantity + fill.fee;

        // Clamp quantity if cash would go negative
        let affordable_qty = if cost > self.state.cash {
            let max_cost = self.state.cash;
            // qty = (max_cost - fee_for_that_qty) / fill.price
            // fee = fill.price * qty * fee_rate → solve: qty = max_cost / (fill.price * (1 + fee_rate))
            // But we don't have fee_rate here. Approximate: reduce quantity proportionally.
            fill.quantity * (self.state.cash / cost)
        } else {
            fill.quantity
        };

        if affordable_qty <= 0.0 {
            return;
        }

        let actual_cost = fill.price * affordable_qty + (fill.fee * affordable_qty / fill.quantity);
        self.state.cash -= actual_cost;

        match &mut self.state.position {
            Some(pos) => {
                // Average into existing position (shouldn't happen in MVP, but defensive)
                let total_qty = pos.quantity + affordable_qty;
                pos.entry_price = (pos.entry_price * pos.quantity + fill.price * affordable_qty) / total_qty;
                pos.quantity = total_qty;
            }
            None => {
                self.state.position = Some(Position {
                    entry_price: fill.price,
                    quantity: affordable_qty,
                    entry_time: fill.timestamp,
                    unrealized_pnl: 0.0,
                });
            }
        }
    }

    fn apply_sell(&mut self, fill: &Fill) {
        let Some(pos) = &self.state.position else {
            return; // Defensive: no position to close
        };

        let realized_pnl = (fill.price - pos.entry_price) * fill.quantity - fill.fee;
        let proceeds = fill.price * fill.quantity - fill.fee;

        self.state.cash += proceeds;
        self.state.total_pnl += realized_pnl;
        self.state.position = None;
    }

    pub fn mark_to_market(&mut self, bar: &Bar) {
        if let Some(ref mut pos) = self.state.position {
            pos.unrealized_pnl = (bar.close - pos.entry_price) * pos.quantity;
            self.state.equity = self.state.cash + pos.quantity * bar.close;
        } else {
            self.state.equity = self.state.cash;
        }
        self.state.peak_equity = self.state.peak_equity.max(self.state.equity);
    }

    pub fn current_drawdown(&self) -> f64 {
        if self.state.peak_equity <= 0.0 {
            return 0.0;
        }
        (self.state.peak_equity - self.state.equity) / self.state.peak_equity
    }

    pub fn has_position(&self) -> bool {
        self.state.position.is_some()
    }

    pub fn position_quantity(&self) -> f64 {
        self.state.position.as_ref().expect("no position").quantity
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use approx::assert_relative_eq;

    fn bar_at(close: f64) -> Bar {
        Bar { timestamp: 1000, open: close, high: close, low: close, close, volume: 100.0 }
    }

    #[test]
    fn test_new_portfolio() {
        let p = Portfolio::new(10_000.0);
        assert_relative_eq!(p.state.equity, 10_000.0);
        assert_relative_eq!(p.state.cash, 10_000.0);
        assert!(p.state.position.is_none());
        assert!(!p.state.halted);
    }

    #[test]
    fn test_buy_fill() {
        let mut p = Portfolio::new(10_000.0);
        let fill = Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 1.0, timestamp: 1000,
        };
        p.apply_fills(&[fill]);
        // cost = 100*10 + 1 = 1001
        assert_relative_eq!(p.state.cash, 10_000.0 - 1001.0);
        assert!(p.has_position());
        assert_relative_eq!(p.position_quantity(), 10.0);
    }

    #[test]
    fn test_sell_fill_with_profit() {
        let mut p = Portfolio::new(10_000.0);
        // Buy at 100
        p.apply_fills(&[Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 1.0, timestamp: 1000,
        }]);
        // Sell at 110
        p.apply_fills(&[Fill {
            order_id: 2, side: OrderSide::Sell, price: 110.0,
            quantity: 10.0, fee: 1.1, timestamp: 2000,
        }]);
        assert!(!p.has_position());
        // realized_pnl = (110-100)*10 - 1.1 = 98.9
        assert_relative_eq!(p.state.total_pnl, 98.9, epsilon = 1e-10);
        // cash = (10000 - 1001) + (110*10 - 1.1) = 8999 + 1098.9 = 10097.9
        assert_relative_eq!(p.state.cash, 10_097.9, epsilon = 1e-10);
    }

    #[test]
    fn test_mark_to_market() {
        let mut p = Portfolio::new(10_000.0);
        p.apply_fills(&[Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 0.0, timestamp: 1000,
        }]);
        // cash = 10000 - 1000 = 9000, holding 10 @ 100
        p.mark_to_market(&bar_at(120.0));
        // equity = 9000 + 10*120 = 10200
        assert_relative_eq!(p.state.equity, 10_200.0);
        assert_relative_eq!(p.state.peak_equity, 10_200.0);
    }

    #[test]
    fn test_drawdown() {
        let mut p = Portfolio::new(10_000.0);
        p.apply_fills(&[Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 0.0, timestamp: 1000,
        }]);
        p.mark_to_market(&bar_at(120.0)); // equity = 10200, peak = 10200
        p.mark_to_market(&bar_at(90.0));  // equity = 9000 + 10*90 = 9900
        // drawdown = (10200 - 9900) / 10200 = 0.02941...
        assert_relative_eq!(p.current_drawdown(), 300.0 / 10_200.0, epsilon = 1e-10);
    }

    #[test]
    fn test_buy_clamps_to_affordable() {
        let mut p = Portfolio::new(100.0);
        let fill = Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 10.0, timestamp: 1000,
        };
        // cost = 100*10 + 10 = 1010, but only have 100
        p.apply_fills(&[fill]);
        // Should clamp: affordable_qty ≈ 10 * (100/1010) ≈ 0.99
        assert!(p.state.cash >= 0.0);
        assert!(p.has_position());
        assert!(p.position_quantity() < 10.0);
    }
}
```

Update `lib.rs`:

```rust
pub mod types;
pub mod broker;
pub mod portfolio;

pub use types::*;
```

**Verify:** `cargo test -p bt-engine -- portfolio` — all 5 tests pass.

### Task 4.2: Commit portfolio

```bash
git add crates/bt-engine/src/portfolio.rs crates/bt-engine/src/lib.rs
git commit -m "Add Portfolio: apply_fills, mark_to_market, drawdown tracking, cash clamping"
```

---

## Work Stream 5: Risk Manager (risk.rs)

### Task 5.1: Implement RiskManager

File: `crates/bt-engine/src/risk.rs`

```rust
use crate::types::*;
use crate::portfolio::Portfolio;

pub struct RiskManager {
    config: RiskConfig,
    next_order_id: u64,
}

impl RiskManager {
    pub fn new(config: RiskConfig) -> Self {
        Self { config, next_order_id: 1 }
    }

    fn next_id(&mut self) -> u64 {
        let id = self.next_order_id;
        self.next_order_id += 1;
        id
    }

    pub fn gate_signal(&mut self, signal: &Signal, portfolio: &Portfolio, bar: &Bar) -> Option<Order> {
        let want_long = signal.strength >= self.config.signal_threshold;
        let is_holding = portfolio.has_position();

        if want_long && !is_holding {
            let capped_size = signal.target_size.min(self.config.max_position_size);
            if capped_size <= 0.0 || bar.close <= 0.0 {
                return None;
            }
            let qty = (portfolio.state.equity * capped_size) / bar.close;
            if qty <= 0.0 {
                return None;
            }
            Some(Order {
                id: self.next_id(),
                side: OrderSide::Buy,
                quantity: qty,
                created_at: bar.timestamp,
            })
        } else if !want_long && is_holding {
            let qty = portfolio.position_quantity();
            Some(Order {
                id: self.next_id(),
                side: OrderSide::Sell,
                quantity: qty,
                created_at: bar.timestamp,
            })
        } else {
            None
        }
    }

    pub fn should_halt(&self, portfolio: &Portfolio) -> bool {
        portfolio.current_drawdown() >= self.config.max_drawdown_halt
    }

    pub fn build_emergency_close(&mut self, portfolio: &Portfolio, bar: &Bar) -> Order {
        Order {
            id: self.next_id(),
            side: OrderSide::Sell,
            quantity: portfolio.position_quantity(),
            created_at: bar.timestamp,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use approx::assert_relative_eq;

    fn risk_config() -> RiskConfig {
        RiskConfig {
            max_position_size: 0.2,
            max_drawdown_halt: 0.15,
            signal_threshold: 0.5,
        }
    }

    fn bar_at(close: f64) -> Bar {
        Bar { timestamp: 1000, open: close, high: close, low: close, close, volume: 100.0 }
    }

    #[test]
    fn test_signal_above_threshold_opens_long() {
        let mut risk = RiskManager::new(risk_config());
        let portfolio = Portfolio::new(10_000.0);
        let signal = Signal { strength: 0.8, target_size: 0.15 };
        let bar = bar_at(100.0);

        let order = risk.gate_signal(&signal, &portfolio, &bar);
        assert!(order.is_some());
        let order = order.unwrap();
        assert_eq!(order.side, OrderSide::Buy);
        // qty = 10000 * 0.15 / 100 = 15
        assert_relative_eq!(order.quantity, 15.0);
    }

    #[test]
    fn test_signal_below_threshold_when_flat() {
        let mut risk = RiskManager::new(risk_config());
        let portfolio = Portfolio::new(10_000.0);
        let signal = Signal { strength: 0.3, target_size: 0.15 };

        let order = risk.gate_signal(&signal, &portfolio, &bar_at(100.0));
        assert!(order.is_none()); // Already flat, want flat → no order
    }

    #[test]
    fn test_caps_position_size() {
        let mut risk = RiskManager::new(risk_config()); // max_position_size = 0.2
        let portfolio = Portfolio::new(10_000.0);
        let signal = Signal { strength: 0.8, target_size: 0.5 }; // wants 50%, capped to 20%
        let bar = bar_at(100.0);

        let order = risk.gate_signal(&signal, &portfolio, &bar).unwrap();
        // qty = 10000 * 0.2 / 100 = 20 (capped from 50)
        assert_relative_eq!(order.quantity, 20.0);
    }

    #[test]
    fn test_signal_below_threshold_closes_position() {
        let mut risk = RiskManager::new(risk_config());
        let mut portfolio = Portfolio::new(10_000.0);
        // Open a position
        portfolio.apply_fills(&[Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 0.0, timestamp: 1000,
        }]);

        let signal = Signal { strength: 0.2, target_size: 0.0 }; // below threshold
        let order = risk.gate_signal(&signal, &portfolio, &bar_at(100.0));
        assert!(order.is_some());
        let order = order.unwrap();
        assert_eq!(order.side, OrderSide::Sell);
        assert_relative_eq!(order.quantity, 10.0);
    }

    #[test]
    fn test_duplicate_long_signal_no_order() {
        let mut risk = RiskManager::new(risk_config());
        let mut portfolio = Portfolio::new(10_000.0);
        portfolio.apply_fills(&[Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 0.0, timestamp: 1000,
        }]);

        let signal = Signal { strength: 0.8, target_size: 0.15 }; // above threshold, already long
        let order = risk.gate_signal(&signal, &portfolio, &bar_at(100.0));
        assert!(order.is_none()); // No resizing in MVP
    }

    #[test]
    fn test_drawdown_halt() {
        let mut portfolio = Portfolio::new(10_000.0);
        portfolio.state.peak_equity = 10_000.0;
        portfolio.state.equity = 8_000.0; // 20% drawdown
        let risk = RiskManager::new(risk_config()); // halt at 15%
        assert!(risk.should_halt(&portfolio));
    }
}
```

Update `lib.rs`:

```rust
pub mod types;
pub mod broker;
pub mod portfolio;
pub mod risk;

pub use types::*;
```

**Verify:** `cargo test -p bt-engine -- risk` — all 6 tests pass.

### Task 5.2: Commit risk

```bash
git add crates/bt-engine/src/risk.rs crates/bt-engine/src/lib.rs
git commit -m "Add RiskManager: signal gating, position size caps, drawdown halt, emergency close"
```

---

## Work Stream 6: Tick + Engine (tick.rs, engine.rs)

### Task 6.1: Implement tick (single-bar state transition)

File: `crates/bt-engine/src/tick.rs`

```rust
use crate::broker::Broker;
use crate::portfolio::Portfolio;
use crate::risk::RiskManager;
use crate::types::*;

pub fn tick(
    bar: &Bar,
    signal: &Signal,
    broker: &mut Broker,
    portfolio: &mut Portfolio,
    risk: &mut RiskManager,
    fill_model: FillModel,
    equity_curve: &mut Vec<(i64, f64)>,
    all_fills: &mut Vec<Fill>,
    halted_at: &mut Option<i64>,
) {
    let signal = Signal {
        strength: signal.strength.clamp(0.0, 1.0),
        target_size: signal.target_size.clamp(0.0, 1.0),
    };

    // 1. Fill pending orders from previous bar
    let pending_fills = broker.fill_pending(bar);
    for fill in &pending_fills {
        all_fills.push(fill.clone());
    }
    portfolio.apply_fills(&pending_fills);

    // 2. Mark to market FIRST
    portfolio.mark_to_market(bar);

    // 3. Drawdown halt check
    if !portfolio.state.halted && risk.should_halt(portfolio) {
        if portfolio.has_position() {
            let close_order = risk.build_emergency_close(portfolio, bar);
            let fill = broker.fill_immediate(&close_order, bar);
            all_fills.push(fill.clone());
            portfolio.apply_fills(&[fill]);
            portfolio.mark_to_market(bar);
        }
        portfolio.state.halted = true;
        *halted_at = Some(bar.timestamp);
        equity_curve.push((bar.timestamp, portfolio.state.equity));
        return;
    }

    // 4. Already halted — just record equity
    if portfolio.state.halted {
        equity_curve.push((bar.timestamp, portfolio.state.equity));
        return;
    }

    // 5. Signal → order
    if let Some(order) = risk.gate_signal(&signal, portfolio, bar) {
        match fill_model {
            FillModel::Immediate => {
                let fill = broker.fill_immediate(&order, bar);
                all_fills.push(fill.clone());
                portfolio.apply_fills(&[fill]);
                portfolio.mark_to_market(bar);
            }
            FillModel::NextBar => {
                broker.cancel_pending();
                broker.enqueue_pending(order);
            }
        }
    }

    // 6. Record equity
    equity_curve.push((bar.timestamp, portfolio.state.equity));
}
```

### Task 6.2: Implement Engine with metrics computation

File: `crates/bt-engine/src/engine.rs`

```rust
use crate::broker::Broker;
use crate::portfolio::Portfolio;
use crate::risk::RiskManager;
use crate::tick::tick;
use crate::types::*;

pub struct BacktestEngine {
    broker: Broker,
    portfolio: Portfolio,
    risk: RiskManager,
    config: EngineConfig,
    equity_curve: Vec<(i64, f64)>,
    fills: Vec<Fill>,
    halted_at: Option<i64>,
}

impl BacktestEngine {
    pub fn new(config: EngineConfig) -> Result<Self, EngineError> {
        if config.initial_capital <= 0.0 {
            return Err(EngineError::InvalidConfig("initial_capital must be > 0".into()));
        }
        if config.bars_per_year <= 0.0 {
            return Err(EngineError::InvalidConfig("bars_per_year must be > 0".into()));
        }
        Ok(Self {
            broker: Broker::new(config.broker.clone()),
            portfolio: Portfolio::new(config.initial_capital),
            risk: RiskManager::new(config.risk.clone()),
            config,
            equity_curve: Vec::new(),
            fills: Vec::new(),
            halted_at: None,
        })
    }

    pub fn run(&mut self, bars: &[Bar], signals: &[Signal]) -> Result<BacktestResult, EngineError> {
        if bars.len() != signals.len() {
            return Err(EngineError::LengthMismatch {
                bars: bars.len(),
                signals: signals.len(),
            });
        }
        if bars.is_empty() {
            return Err(EngineError::EmptyInput);
        }

        self.equity_curve.reserve(bars.len());

        for (bar, signal) in bars.iter().zip(signals.iter()) {
            tick(
                bar,
                signal,
                &mut self.broker,
                &mut self.portfolio,
                &mut self.risk,
                self.config.broker.fill_model,
                &mut self.equity_curve,
                &mut self.fills,
                &mut self.halted_at,
            );
        }

        Ok(self.build_result())
    }

    fn build_result(&self) -> BacktestResult {
        let metrics = self.compute_metrics();
        BacktestResult {
            equity_curve: self.equity_curve.clone(),
            fills: self.fills.clone(),
            final_state: self.portfolio.state.clone(),
            metrics,
            halted_at: self.halted_at,
        }
    }

    fn compute_metrics(&self) -> ResultMetrics {
        let initial = self.config.initial_capital;
        let final_equity = self.portfolio.state.equity;
        let total_return = (final_equity - initial) / initial;

        // Bar returns
        let returns: Vec<f64> = self.equity_curve.windows(2)
            .map(|w| {
                if w[0].1 == 0.0 { 0.0 } else { (w[1].1 - w[0].1) / w[0].1 }
            })
            .collect();

        let n = returns.len() as f64;
        let bpy = self.config.bars_per_year;

        // Sharpe
        let mean_ret = if n > 0.0 { returns.iter().sum::<f64>() / n } else { 0.0 };
        let std_ret = std_dev(&returns, mean_ret);
        let sharpe = if std_ret > 0.0 { mean_ret / std_ret * bpy.sqrt() } else { 0.0 };

        // Sortino
        let downside_sq_sum: f64 = returns.iter()
            .map(|r| { let d = r.min(0.0); d * d })
            .sum();
        let downside_dev = if n > 0.0 { (downside_sq_sum / n).sqrt() } else { 0.0 };
        let sortino = if downside_dev > 0.0 { mean_ret / downside_dev * bpy.sqrt() } else { 0.0 };

        // Max drawdown (already tracked by portfolio, but recompute from equity curve for accuracy)
        let max_dd = compute_max_drawdown(&self.equity_curve);

        // Round trips
        let (num_round_trips, win_rate, profit_factor) = self.compute_round_trips();

        ResultMetrics {
            total_return,
            sharpe_ratio: sharpe,
            sortino_ratio: sortino,
            max_drawdown: max_dd,
            num_round_trips,
            win_rate,
            profit_factor,
            bars_per_year: bpy,
        }
    }

    fn compute_round_trips(&self) -> (u32, f64, f64) {
        let mut trips: Vec<f64> = Vec::new();
        let mut buy_price = 0.0;
        let mut buy_qty = 0.0;
        let mut buy_fee = 0.0;

        for fill in &self.fills {
            match fill.side {
                OrderSide::Buy => {
                    buy_price = fill.price;
                    buy_qty = fill.quantity;
                    buy_fee = fill.fee;
                }
                OrderSide::Sell => {
                    if buy_qty > 0.0 {
                        let pnl = (fill.price - buy_price) * fill.quantity - fill.fee - buy_fee;
                        trips.push(pnl);
                        buy_qty = 0.0;
                    }
                }
            }
        }

        let num = trips.len() as u32;
        if num == 0 {
            return (0, 0.0, 0.0);
        }

        let winners: f64 = trips.iter().filter(|&&p| p > 0.0).count() as f64;
        let win_rate = winners / num as f64;

        let gross_profit: f64 = trips.iter().filter(|&&p| p > 0.0).sum();
        let gross_loss: f64 = trips.iter().filter(|&&p| p < 0.0).sum::<f64>().abs();
        let profit_factor = if gross_loss > 0.0 {
            gross_profit / gross_loss
        } else if gross_profit > 0.0 {
            f64::INFINITY
        } else {
            0.0
        };

        (num, win_rate, profit_factor)
    }
}

fn std_dev(values: &[f64], mean: f64) -> f64 {
    if values.len() < 2 { return 0.0; }
    let variance: f64 = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / (values.len() as f64 - 1.0);
    variance.sqrt()
}

fn compute_max_drawdown(equity_curve: &[(i64, f64)]) -> f64 {
    let mut peak = 0.0_f64;
    let mut max_dd = 0.0_f64;
    for &(_, equity) in equity_curve {
        peak = peak.max(equity);
        if peak > 0.0 {
            let dd = (peak - equity) / peak;
            max_dd = max_dd.max(dd);
        }
    }
    max_dd
}

#[cfg(test)]
mod tests {
    use super::*;
    use approx::assert_relative_eq;

    fn make_config() -> EngineConfig {
        EngineConfig {
            initial_capital: 10_000.0,
            broker: BrokerConfig {
                slippage_bps: 0.0, // zero slippage for predictable tests
                fee_rate: 0.0,     // zero fees for predictable tests
                fill_model: FillModel::Immediate,
            },
            risk: RiskConfig {
                max_position_size: 1.0,
                max_drawdown_halt: 0.50, // high threshold so it doesn't trigger
                signal_threshold: 0.5,
            },
            bars_per_year: 8760.0,
        }
    }

    fn bars_from_closes(closes: &[f64]) -> Vec<Bar> {
        closes.iter().enumerate().map(|(i, &c)| Bar {
            timestamp: (i as i64 + 1) * 3_600_000,
            open: c, high: c, low: c, close: c, volume: 100.0,
        }).collect()
    }

    #[test]
    fn test_no_signals_no_trades() {
        let mut engine = BacktestEngine::new(make_config()).unwrap();
        let bars = bars_from_closes(&[100.0, 101.0, 102.0]);
        let signals = vec![
            Signal { strength: 0.0, target_size: 0.0 }; 3
        ];
        let result = engine.run(&bars, &signals).unwrap();
        assert_eq!(result.fills.len(), 0);
        assert_eq!(result.metrics.num_round_trips, 0);
        assert_relative_eq!(result.metrics.total_return, 0.0);
    }

    #[test]
    fn test_buy_hold_sell_profit() {
        let mut engine = BacktestEngine::new(make_config()).unwrap();
        let bars = bars_from_closes(&[100.0, 110.0, 120.0, 130.0, 120.0]);
        let signals = vec![
            Signal { strength: 1.0, target_size: 1.0 }, // buy at bar 0 close (100)
            Signal { strength: 1.0, target_size: 1.0 }, // hold
            Signal { strength: 1.0, target_size: 1.0 }, // hold
            Signal { strength: 0.0, target_size: 0.0 }, // sell at bar 3 close (130)
            Signal { strength: 0.0, target_size: 0.0 }, // flat
        ];
        let result = engine.run(&bars, &signals).unwrap();
        assert_eq!(result.fills.len(), 2); // 1 buy + 1 sell
        assert_eq!(result.metrics.num_round_trips, 1);
        // Bought 100 units at 100, sold at 130 → profit = 3000 on 10000 = 30%
        assert_relative_eq!(result.metrics.total_return, 0.3, epsilon = 1e-6);
    }

    #[test]
    fn test_length_mismatch_error() {
        let mut engine = BacktestEngine::new(make_config()).unwrap();
        let bars = bars_from_closes(&[100.0, 110.0]);
        let signals = vec![Signal { strength: 0.0, target_size: 0.0 }]; // length 1 vs 2
        let result = engine.run(&bars, &signals);
        assert!(result.is_err());
    }

    #[test]
    fn test_empty_input_error() {
        let mut engine = BacktestEngine::new(make_config()).unwrap();
        let result = engine.run(&[], &[]);
        assert!(result.is_err());
    }

    #[test]
    fn test_drawdown_halt() {
        let config = EngineConfig {
            risk: RiskConfig {
                max_position_size: 1.0,
                max_drawdown_halt: 0.10, // halt at 10% drawdown
                signal_threshold: 0.5,
            },
            ..make_config()
        };
        let mut engine = BacktestEngine::new(config).unwrap();
        let bars = bars_from_closes(&[100.0, 50.0, 60.0, 70.0]);
        let signals = vec![
            Signal { strength: 1.0, target_size: 1.0 }, // buy at 100
            Signal { strength: 1.0, target_size: 1.0 }, // price drops to 50 → ~50% DD → halt
            Signal { strength: 1.0, target_size: 1.0 }, // should not trade (halted)
            Signal { strength: 1.0, target_size: 1.0 }, // should not trade (halted)
        ];
        let result = engine.run(&bars, &signals).unwrap();
        assert!(result.halted_at.is_some());
        assert!(result.final_state.halted);
        // Position should have been liquidated at halt
        assert!(result.final_state.position.is_none());
    }

    #[test]
    fn test_next_bar_fill_model() {
        let config = EngineConfig {
            broker: BrokerConfig {
                slippage_bps: 0.0,
                fee_rate: 0.0,
                fill_model: FillModel::NextBar,
            },
            ..make_config()
        };
        let mut engine = BacktestEngine::new(config).unwrap();
        let bars = bars_from_closes(&[100.0, 105.0, 110.0, 100.0]);
        let signals = vec![
            Signal { strength: 1.0, target_size: 1.0 }, // signal to buy (enqueued)
            Signal { strength: 1.0, target_size: 1.0 }, // filled at bar 1 open (105)
            Signal { strength: 0.0, target_size: 0.0 }, // signal to sell (enqueued)
            Signal { strength: 0.0, target_size: 0.0 }, // filled at bar 3 open (100)
        ];
        let result = engine.run(&bars, &signals).unwrap();

        // Buy fill at bar 1 open = 105, sell fill at bar 3 open = 100
        assert_eq!(result.fills.len(), 2);
        assert_relative_eq!(result.fills[0].price, 105.0); // bought at open of bar 1
        assert_relative_eq!(result.fills[1].price, 100.0); // sold at open of bar 3
    }
}
```

Update `lib.rs`:

```rust
pub mod types;
pub mod broker;
pub mod portfolio;
pub mod risk;
pub mod tick;
pub mod engine;

pub use types::*;
pub use engine::BacktestEngine;
```

**Verify:** `cargo test -p bt-engine` — all tests pass (broker: 5, portfolio: 5, risk: 6, engine: 6 = 22 total).

### Task 6.3: Commit engine

```bash
git add crates/bt-engine/src/
git commit -m "Add tick state transition + BacktestEngine: batch run, metrics computation, full test suite"
```

---

## Work Stream 7: PyO3 Bindings (python.rs)

### Task 7.1: Implement PyO3 bindings

File: `crates/bt-engine/src/python.rs`

```rust
use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::engine::BacktestEngine as RustEngine;
use crate::types::*;

#[pyclass(name = "BacktestEngine")]
pub struct PyBacktestEngine {
    config: EngineConfig,
}

#[pymethods]
impl PyBacktestEngine {
    #[new]
    fn new(
        initial_capital: f64,
        broker: &Bound<'_, PyDict>,
        risk: &Bound<'_, PyDict>,
        bars_per_year: f64,
    ) -> PyResult<Self> {
        let slippage_bps: f64 = broker.get_item("slippage_bps")?.unwrap().extract()?;
        let fee_rate: f64 = broker.get_item("fee_rate")?.unwrap().extract()?;
        let fill_model_str: String = broker.get_item("fill_model")?.unwrap().extract()?;
        let fill_model = match fill_model_str.as_str() {
            "immediate" => FillModel::Immediate,
            "next_bar" => FillModel::NextBar,
            other => return Err(pyo3::exceptions::PyValueError::new_err(
                format!("invalid fill_model: '{}', expected 'immediate' or 'next_bar'", other),
            )),
        };

        let max_position_size: f64 = risk.get_item("max_position_size")?.unwrap().extract()?;
        let max_drawdown_halt: f64 = risk.get_item("max_drawdown_halt")?.unwrap().extract()?;
        let signal_threshold: f64 = risk.get_item("signal_threshold")?.unwrap().extract()?;

        Ok(Self {
            config: EngineConfig {
                initial_capital,
                broker: BrokerConfig { slippage_bps, fee_rate, fill_model },
                risk: RiskConfig { max_position_size, max_drawdown_halt, signal_threshold },
                bars_per_year,
            },
        })
    }

    fn run<'py>(
        &self,
        py: Python<'py>,
        timestamps: PyReadonlyArray1<'py, i64>,
        opens: PyReadonlyArray1<'py, f64>,
        highs: PyReadonlyArray1<'py, f64>,
        lows: PyReadonlyArray1<'py, f64>,
        closes: PyReadonlyArray1<'py, f64>,
        volumes: PyReadonlyArray1<'py, f64>,
        strengths: PyReadonlyArray1<'py, f64>,
        target_sizes: PyReadonlyArray1<'py, f64>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let ts = timestamps.as_slice()?;
        let o = opens.as_slice()?;
        let h = highs.as_slice()?;
        let l = lows.as_slice()?;
        let c = closes.as_slice()?;
        let v = volumes.as_slice()?;
        let str_arr = strengths.as_slice()?;
        let ts_arr = target_sizes.as_slice()?;

        let bars: Vec<Bar> = (0..ts.len())
            .map(|i| Bar {
                timestamp: ts[i],
                open: o[i],
                high: h[i],
                low: l[i],
                close: c[i],
                volume: v[i],
            })
            .collect();

        let signals: Vec<Signal> = (0..str_arr.len())
            .map(|i| Signal {
                strength: str_arr[i],
                target_size: ts_arr[i],
            })
            .collect();

        let mut engine = RustEngine::new(self.config.clone())
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        let result = engine
            .run(&bars, &signals)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

        // Build result dict
        let dict = PyDict::new(py);

        // Equity curve as 2D numpy array
        let eq_ts: Vec<i64> = result.equity_curve.iter().map(|e| e.0).collect();
        let eq_vals: Vec<f64> = result.equity_curve.iter().map(|e| e.1).collect();
        dict.set_item("equity_timestamps", PyArray1::from_vec(py, eq_ts))?;
        dict.set_item("equity_values", PyArray1::from_vec(py, eq_vals))?;

        // Fills as list of dicts
        let fills_list: Vec<Bound<'py, PyDict>> = result.fills.iter().map(|f| {
            let d = PyDict::new(py);
            d.set_item("order_id", f.order_id).unwrap();
            d.set_item("side", match f.side { OrderSide::Buy => "buy", OrderSide::Sell => "sell" }).unwrap();
            d.set_item("price", f.price).unwrap();
            d.set_item("quantity", f.quantity).unwrap();
            d.set_item("fee", f.fee).unwrap();
            d.set_item("timestamp", f.timestamp).unwrap();
            d
        }).collect();
        dict.set_item("fills", fills_list)?;

        // Metrics
        let m = &result.metrics;
        let metrics = PyDict::new(py);
        metrics.set_item("total_return", m.total_return)?;
        metrics.set_item("sharpe_ratio", m.sharpe_ratio)?;
        metrics.set_item("sortino_ratio", m.sortino_ratio)?;
        metrics.set_item("max_drawdown", m.max_drawdown)?;
        metrics.set_item("num_round_trips", m.num_round_trips)?;
        metrics.set_item("win_rate", m.win_rate)?;
        metrics.set_item("profit_factor", m.profit_factor)?;
        metrics.set_item("bars_per_year", m.bars_per_year)?;
        dict.set_item("metrics", metrics)?;

        // Final state
        let fs = &result.final_state;
        let state = PyDict::new(py);
        state.set_item("equity", fs.equity)?;
        state.set_item("cash", fs.cash)?;
        state.set_item("peak_equity", fs.peak_equity)?;
        state.set_item("total_pnl", fs.total_pnl)?;
        state.set_item("halted", fs.halted)?;
        state.set_item("has_position", fs.position.is_some())?;
        dict.set_item("final_state", state)?;

        dict.set_item("halted_at", result.halted_at)?;

        Ok(dict)
    }
}

#[pymodule]
fn bt_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyBacktestEngine>()?;
    Ok(())
}
```

Update `lib.rs` to include python module:

```rust
pub mod types;
pub mod broker;
pub mod portfolio;
pub mod risk;
pub mod tick;
pub mod engine;
pub mod python;

pub use types::*;
pub use engine::BacktestEngine;
```

**Verify:** `cargo check -p bt-engine` succeeds.

### Task 7.2: Build and test Python bindings

```bash
cd /Users/sskys/Mine/backtest
uv venv
uv pip install maturin
maturin develop
python -c "from bt.bt_engine import BacktestEngine; print('OK')"
```

### Task 7.3: Write Python integration test

File: `tests/python/test_engine_integration.py`

```python
import numpy as np
import pytest


def test_engine_basic_round_trip():
    from bt.bt_engine import BacktestEngine

    engine = BacktestEngine(
        initial_capital=10_000.0,
        broker={"slippage_bps": 0.0, "fee_rate": 0.0, "fill_model": "immediate"},
        risk={"max_position_size": 1.0, "max_drawdown_halt": 0.5, "signal_threshold": 0.5},
        bars_per_year=8760.0,
    )

    n = 5
    timestamps = np.arange(1, n + 1, dtype=np.int64) * 3_600_000
    closes = np.array([100.0, 110.0, 120.0, 130.0, 120.0])
    opens = closes.copy()
    highs = closes.copy()
    lows = closes.copy()
    volumes = np.full(n, 100.0)

    strengths = np.array([1.0, 1.0, 1.0, 0.0, 0.0])
    target_sizes = np.array([1.0, 1.0, 1.0, 0.0, 0.0])

    result = engine.run(
        timestamps=timestamps, opens=opens, highs=highs, lows=lows,
        closes=closes, volumes=volumes, strengths=strengths, target_sizes=target_sizes,
    )

    assert result["metrics"]["num_round_trips"] == 1
    assert result["metrics"]["total_return"] == pytest.approx(0.3, abs=1e-6)
    assert len(result["fills"]) == 2
    assert result["fills"][0]["side"] == "buy"
    assert result["fills"][1]["side"] == "sell"
    assert result["halted_at"] is None


def test_engine_empty_signals_no_trades():
    from bt.bt_engine import BacktestEngine

    engine = BacktestEngine(
        initial_capital=10_000.0,
        broker={"slippage_bps": 5.0, "fee_rate": 0.001, "fill_model": "next_bar"},
        risk={"max_position_size": 0.2, "max_drawdown_halt": 0.15, "signal_threshold": 0.5},
        bars_per_year=8760.0,
    )

    n = 3
    timestamps = np.arange(1, n + 1, dtype=np.int64) * 3_600_000
    prices = np.array([100.0, 101.0, 102.0])
    zeros = np.zeros(n)

    result = engine.run(
        timestamps=timestamps, opens=prices, highs=prices, lows=prices,
        closes=prices, volumes=np.full(n, 100.0), strengths=zeros, target_sizes=zeros,
    )

    assert result["metrics"]["num_round_trips"] == 0
    assert result["metrics"]["total_return"] == pytest.approx(0.0)
    assert result["final_state"]["equity"] == pytest.approx(10_000.0)


def test_engine_length_mismatch():
    from bt.bt_engine import BacktestEngine

    engine = BacktestEngine(
        initial_capital=10_000.0,
        broker={"slippage_bps": 0.0, "fee_rate": 0.0, "fill_model": "immediate"},
        risk={"max_position_size": 1.0, "max_drawdown_halt": 0.5, "signal_threshold": 0.5},
        bars_per_year=8760.0,
    )

    with pytest.raises(ValueError, match="length"):
        engine.run(
            timestamps=np.array([1], dtype=np.int64),
            opens=np.array([100.0]),
            highs=np.array([100.0]),
            lows=np.array([100.0]),
            closes=np.array([100.0]),
            volumes=np.array([100.0]),
            strengths=np.array([0.0, 0.0]),  # wrong length
            target_sizes=np.array([0.0, 0.0]),
        )
```

**Verify:** `uv run pytest tests/python/test_engine_integration.py -v` — all 3 tests pass.

### Task 7.4: Commit PyO3 bindings

```bash
git add crates/bt-engine/src/python.rs crates/bt-engine/src/lib.rs tests/python/test_engine_integration.py
git commit -m "Add PyO3 bindings: BacktestEngine accessible from Python, NumPy array I/O, integration tests"
```

---

## Verification Checklist

After completing all tasks:

1. `cargo test -p bt-engine` — **22+ Rust tests pass**
2. `maturin develop` — builds without errors
3. `uv run pytest tests/python/ -v` — **3+ Python integration tests pass**
4. `python -c "from bt.bt_engine import BacktestEngine; print('OK')"` — prints OK

---

## End of Plan A

Plan A produces: a working Rust backtest engine with PyO3 bindings, fully tested from both Rust and Python. This is the foundation for Plans B, C, D.
