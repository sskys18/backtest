use crate::broker::Broker;
use crate::portfolio::Portfolio;
use crate::risk::RiskManager;
use crate::tick::tick;
use crate::types::*;

/// Per-step engine for RL training. Unlike BacktestEngine which takes
/// all bars at once, StepEngine advances one bar at a time so Python
/// can observe state and choose actions between bars.
pub struct StepEngine {
    broker: Broker,
    portfolio: Portfolio,
    risk: RiskManager,
    config: EngineConfig,
    bars: Vec<Bar>,
    current_step: usize,
    equity_curve: Vec<(i64, f64)>,
    fills: Vec<Fill>,
    halted_at: Option<i64>,
    prev_equity: f64,
}

/// Observation returned to Python after each step.
#[derive(Debug, Clone)]
pub struct StepObs {
    /// Normalized OHLCV of current bar
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    /// Portfolio state
    pub equity: f64,
    pub cash: f64,
    pub position_qty: f64,
    pub position_entry_price: f64,
    pub unrealized_pnl: f64,
    pub drawdown: f64,
    /// Step metadata
    pub step: usize,
    pub halted: bool,
}

/// Result of a single step.
pub struct StepResult {
    pub obs: StepObs,
    pub reward: f64,
    pub done: bool,
    pub truncated: bool,
}

impl StepEngine {
    pub fn new(config: EngineConfig, bars: Vec<Bar>) -> Result<Self, EngineError> {
        if config.initial_capital <= 0.0 {
            return Err(EngineError::InvalidConfig("initial_capital must be > 0".into()));
        }
        if bars.is_empty() {
            return Err(EngineError::EmptyInput);
        }
        let initial = config.initial_capital;
        Ok(Self {
            broker: Broker::new(config.broker.clone()),
            portfolio: Portfolio::new(config.initial_capital),
            risk: RiskManager::new(config.risk.clone()),
            config,
            bars,
            current_step: 0,
            equity_curve: Vec::new(),
            fills: Vec::new(),
            halted_at: None,
            prev_equity: initial,
        })
    }

    pub fn reset(&mut self) -> StepObs {
        let initial = self.config.initial_capital;
        self.broker = Broker::new(self.config.broker.clone());
        self.portfolio = Portfolio::new(initial);
        self.risk = RiskManager::new(self.config.risk.clone());
        self.current_step = 0;
        self.equity_curve.clear();
        self.fills.clear();
        self.halted_at = None;
        self.prev_equity = initial;
        self.build_obs()
    }

    /// Step the environment with an action.
    /// action: [strength, target_size] both in [0, 1]
    pub fn step(&mut self, strength: f64, target_size: f64) -> StepResult {
        if self.current_step >= self.bars.len() {
            return StepResult {
                obs: self.build_obs(),
                reward: 0.0,
                done: true,
                truncated: true,
            };
        }

        let bar = self.bars[self.current_step].clone();
        let signal = Signal { strength, target_size };

        self.prev_equity = self.portfolio.state.equity;

        tick(
            &bar,
            &signal,
            &mut self.broker,
            &mut self.portfolio,
            &mut self.risk,
            self.config.broker.fill_model,
            &mut self.equity_curve,
            &mut self.fills,
            &mut self.halted_at,
        );

        self.current_step += 1;

        let done = self.current_step >= self.bars.len() || self.portfolio.state.halted;
        let truncated = self.current_step >= self.bars.len() && !self.portfolio.state.halted;

        // Reward: log return of equity
        let current_equity = self.portfolio.state.equity;
        let reward = if self.prev_equity > 0.0 {
            (current_equity / self.prev_equity).ln()
        } else {
            0.0
        };

        StepResult {
            obs: self.build_obs(),
            reward,
            done,
            truncated,
        }
    }

    fn build_obs(&self) -> StepObs {
        let bar = if self.current_step < self.bars.len() {
            &self.bars[self.current_step]
        } else {
            self.bars.last().unwrap()
        };

        let (pos_qty, pos_entry, unrealized) = match &self.portfolio.state.position {
            Some(p) => (p.quantity, p.entry_price, p.unrealized_pnl),
            None => (0.0, 0.0, 0.0),
        };

        StepObs {
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
            equity: self.portfolio.state.equity,
            cash: self.portfolio.state.cash,
            position_qty: pos_qty,
            position_entry_price: pos_entry,
            unrealized_pnl: unrealized,
            drawdown: self.portfolio.current_drawdown(),
            step: self.current_step,
            halted: self.portfolio.state.halted,
        }
    }

    pub fn num_bars(&self) -> usize {
        self.bars.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_config() -> EngineConfig {
        EngineConfig {
            initial_capital: 10_000.0,
            broker: BrokerConfig {
                slippage_bps: 0.0,
                fee_rate: 0.0,
                fill_model: FillModel::Immediate,
            },
            risk: RiskConfig {
                max_position_size: 1.0,
                max_drawdown_halt: 0.50,
                signal_threshold: 0.5,
            },
            bars_per_year: 8760.0,
        }
    }

    fn make_bars() -> Vec<Bar> {
        vec![
            Bar { timestamp: 1000, open: 100.0, high: 105.0, low: 95.0, close: 102.0, volume: 100.0 },
            Bar { timestamp: 2000, open: 102.0, high: 110.0, low: 100.0, close: 108.0, volume: 100.0 },
            Bar { timestamp: 3000, open: 108.0, high: 112.0, low: 105.0, close: 110.0, volume: 100.0 },
            Bar { timestamp: 4000, open: 110.0, high: 115.0, low: 107.0, close: 112.0, volume: 100.0 },
        ]
    }

    #[test]
    fn test_reset_returns_initial_obs() {
        let mut env = StepEngine::new(make_config(), make_bars()).unwrap();
        let obs = env.reset();
        assert_eq!(obs.step, 0);
        assert!((obs.equity - 10_000.0).abs() < 1e-10);
        assert_eq!(obs.position_qty, 0.0);
        assert!(!obs.halted);
    }

    #[test]
    fn test_step_advances() {
        let mut env = StepEngine::new(make_config(), make_bars()).unwrap();
        env.reset();

        let result = env.step(1.0, 1.0); // buy
        assert_eq!(result.obs.step, 1);
        assert!(!result.done);
        assert!(result.obs.position_qty > 0.0);
    }

    #[test]
    fn test_episode_ends() {
        let mut env = StepEngine::new(make_config(), make_bars()).unwrap();
        env.reset();

        for _ in 0..4 {
            let r = env.step(0.0, 0.0);
            if r.done { break; }
        }
        let r = env.step(0.0, 0.0);
        assert!(r.done);
    }

    #[test]
    fn test_buy_then_sell_profit() {
        let mut env = StepEngine::new(make_config(), make_bars()).unwrap();
        env.reset();

        // Buy at bar 0
        let r1 = env.step(1.0, 1.0);
        assert!(r1.obs.position_qty > 0.0);

        // Hold at bar 1
        let r2 = env.step(1.0, 1.0);
        assert!(r2.reward > 0.0); // price went up

        // Sell at bar 2
        let r3 = env.step(0.0, 0.0);
        assert_eq!(r3.obs.position_qty, 0.0);
    }

    #[test]
    fn test_reward_is_log_return() {
        let mut env = StepEngine::new(make_config(), make_bars()).unwrap();
        env.reset();

        // Stay flat -- equity unchanged
        let r = env.step(0.0, 0.0);
        assert!((r.reward - 0.0).abs() < 1e-10);
    }
}
