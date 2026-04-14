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

    // 5. Signal -> order
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
