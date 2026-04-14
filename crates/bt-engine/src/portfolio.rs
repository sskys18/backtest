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
            return;
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
        assert!(p.state.cash >= 0.0);
        assert!(p.has_position());
        assert!(p.position_quantity() < 10.0);
    }
}
