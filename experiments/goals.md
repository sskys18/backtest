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
