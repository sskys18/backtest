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

        // close=102, slippage=5bps -> 102 * 1.0005 = 102.051
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

        // close=102, slippage=5bps -> 102 * 0.9995 = 101.949
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
        // open=100, slippage=5bps -> 100 * 1.0005 = 100.05
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
