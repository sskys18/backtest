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

        // Max drawdown
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
        assert_eq!(result.fills.len(), 2);
        assert_eq!(result.metrics.num_round_trips, 1);
        assert_relative_eq!(result.metrics.total_return, 0.3, epsilon = 1e-6);
    }

    #[test]
    fn test_length_mismatch_error() {
        let mut engine = BacktestEngine::new(make_config()).unwrap();
        let bars = bars_from_closes(&[100.0, 110.0]);
        let signals = vec![Signal { strength: 0.0, target_size: 0.0 }];
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
                max_drawdown_halt: 0.10,
                signal_threshold: 0.5,
            },
            ..make_config()
        };
        let mut engine = BacktestEngine::new(config).unwrap();
        let bars = bars_from_closes(&[100.0, 50.0, 60.0, 70.0]);
        let signals = vec![
            Signal { strength: 1.0, target_size: 1.0 },
            Signal { strength: 1.0, target_size: 1.0 },
            Signal { strength: 1.0, target_size: 1.0 },
            Signal { strength: 1.0, target_size: 1.0 },
        ];
        let result = engine.run(&bars, &signals).unwrap();
        assert!(result.halted_at.is_some());
        assert!(result.final_state.halted);
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
            Signal { strength: 1.0, target_size: 1.0 },
            Signal { strength: 1.0, target_size: 1.0 },
            Signal { strength: 0.0, target_size: 0.0 },
            Signal { strength: 0.0, target_size: 0.0 },
        ];
        let result = engine.run(&bars, &signals).unwrap();

        assert_eq!(result.fills.len(), 2);
        assert_relative_eq!(result.fills[0].price, 105.0);
        assert_relative_eq!(result.fills[1].price, 100.0);
    }
}
