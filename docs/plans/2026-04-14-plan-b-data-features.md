# Plan B: Data Layer + Feature Engineering

**Spec:** `docs/specs/2026-04-13-backtest-system-design.md` (sections 4, 5, 7.2)
**Scope:** Config system, data schemas/validation/import, feature indicators + builder
**Depends on:** Plan A (project scaffold)
**Status:** Completed 2026-04-14

---

## Work Stream 1: Config System

### Files
- `src/bt/config.py` — YAML loader + Pydantic models

### Models
- `StrategyConfig` — name + params dict
- `BacktestConfig` — pair, timeframe, initial_capital, data_path; `bars_per_year` property
- `BrokerConfig` — slippage_bps, fee_rate, fill_model (validated)
- `RiskConfig` — max_position_size, max_drawdown_halt, signal_threshold
- `Config` — top-level composite
- `load_config(path)` — YAML → Pydantic
- `find_project_root()` — walks up to find pyproject.toml
- `resolve_path(config_path)` — resolves relative to project root

---

## Work Stream 2: Data Schemas + Validation

### Files
- `src/bt/data/schemas.py` — canonical column/dtype defs for 9 data types
- `src/bt/data/validate.py` — DataFrame validation against schemas

### Data Types
OHLCV, funding, OI, depth, trades, liquidations, cross_exchange, FX, records

### Validation Rules
- Shared: monotonic timestamps, no duplicates, no nulls in numeric columns
- OHLCV: low <= open/close <= high, volume >= 0, gap detection (3x median warning)
- `validate_path()` handles single files and directories of parquet files
- Auto-infers data type from column names

---

## Work Stream 3: Data Store + Import

### Files
- `src/bt/data/store.py` — normalize_and_save (cast to canonical types, sort, write parquet)
- `src/bt/data/import_.py` — import CSV/Parquet with validation
- `src/bt/data/sources/base.py` — DataSource protocol (async fetch)

---

## Work Stream 4: Feature Indicators

### Files
- `src/bt/features/indicators.py` — 7 indicators
- `src/bt/features/registry.py` — name → function mapping + `apply_indicator()`

### Indicators
| Name | Inputs | Params | Output |
|------|--------|--------|--------|
| sma | single column | period | rolling mean |
| ema | single column | period | exponential moving average |
| rsi | single column | period | relative strength index (0-100) |
| bollinger | single column | period, num_std | upper, middle, lower bands |
| vwap | high+low+close+volume | period | volume-weighted avg price |
| premium | [global_close, local_close, fx_rate] | — | kimchi premium % |
| funding_ma | funding_rate column | period | SMA of funding rate |

---

## Work Stream 5: Feature Builder

### Files
- `src/bt/features/join.py` — multi-source join logic
- `src/bt/features/build.py` — orchestrates join + indicator computation from YAML

### Join Logic
- OHLCV is backbone (defines output timestamps)
- Other sources: left join by timestamp
- `forward_fill` resample for lower-frequency data
- `aggregate` for trading records (bins into bar intervals)
- Prefix support for cross-exchange columns

---

## Tests (5 files, 11 tests)
- `test_data_validate.py` — 5 tests: valid OHLCV, non-monotonic, OHLC violation, negative volume, empty
- `test_features.py` — 6 tests: SMA basic/length, EMA, RSI range, Bollinger bands/ordering
