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
