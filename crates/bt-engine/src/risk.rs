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
        assert!(order.is_none());
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
        portfolio.apply_fills(&[Fill {
            order_id: 1, side: OrderSide::Buy, price: 100.0,
            quantity: 10.0, fee: 0.0, timestamp: 1000,
        }]);

        let signal = Signal { strength: 0.2, target_size: 0.0 };
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

        let signal = Signal { strength: 0.8, target_size: 0.15 };
        let order = risk.gate_signal(&signal, &portfolio, &bar_at(100.0));
        assert!(order.is_none());
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
