# Plan C: Strategy + Backtest Orchestration + Report

**Spec:** `docs/specs/2026-04-13-backtest-system-design.md` (sections 5, 7.1, 7.3, 7.4, 8)
**Scope:** Strategy protocol + implementations, backtest driver, report generation
**Depends on:** Plan A (Rust engine), Plan B (config + features)
**Status:** Completed 2026-04-14

---

## Work Stream 1: Strategy Layer

### Files
- `src/bt/strategy/base.py` — Strategy protocol + `create_strategy()` factory
- `src/bt/strategy/registry.py` — name → class mapping
- `src/bt/strategy/momentum.py` — SMA crossover
- `src/bt/strategy/mean_revert.py` — Bollinger band bounce

### Strategy Protocol
```python
class Strategy(Protocol):
    def compute(self, df: pl.DataFrame) -> pl.DataFrame:
        # Returns DataFrame with 'strength' and 'target_size' columns
        ...
```

### Momentum Strategy
- Goes long when fast SMA > slow SMA
- Params: fast_period (12), slow_period (26), position_size (0.15)
- Nulls from warmup filled with 0.0

### Mean Revert Strategy
- Goes long when price drops below lower Bollinger Band
- Exits when price returns to middle band
- State-tracking loop (not purely vectorized — iterates for hysteresis)
- Params: bb_period (20), bb_std (2.0), position_size (0.15)

---

## Work Stream 2: Backtest Orchestrator

### Files
- `src/bt/backtest.py` — `run_backtest(config_path)` main entry point

### Pipeline (10 steps from spec section 5)
1. Load config (Pydantic validation)
2. Load features.parquet from `config.backtest.data_path`
3. Validate: check feature df has required columns (fail fast)
4. Instantiate strategy from `config.strategy.name` + params
5. `strategy.compute(df)` → signals DataFrame
6. Validate signals: replace NaN/null with 0.0, warn if any found
7. Python → Rust: pass bars[] + signals[] as NumPy arrays
8. Rust `engine.run()` → BacktestResult
9. Save manifest.json, config.yaml, metrics.json, equity_curve.parquet
10. Return metrics dict

### Run ID Format
`YYYY-MM-DD_{NNN}` — sequential numbering per day

### Manifest
Captures: run_id, timestamp, frozen config, data_hash (SHA256), git_commit, python_version

---

## Work Stream 3: Report

### Files
- `src/bt/report/metrics.py` — `format_metrics()` human-readable display
- `src/bt/report/generate.py` — `generate_report(run_id)` loads saved artifacts

---

## Tests (1 file, 1+6 tests)
- `test_backtest_e2e.py` — full pipeline with synthetic data: config → strategy → engine → saved artifacts verified
- `test_strategy_signals.py` — 6 tests: output shape, no nulls, uptrend signals, position size, mean revert shape/nulls
