# Session Handoff
> Generated: 2026-04-14 01:30

## Task
Build a hybrid Rust+Python crypto backtesting framework (`bt` CLI) with autonomous Claude Code experiment loop.

## Status
### Completed
- Full design spec: `docs/specs/2026-04-13-backtest-system-design.md` (~1100 lines, reviewed 3x by Codex, all blockers fixed)
- **Plan A (Rust engine)**: All 7 work streams implemented and tested
  - types, broker, portfolio, risk, tick, engine, PyO3 bindings
  - 23 Rust tests passing, 3 Python integration tests passing
  - Adapted: PyO3 0.23→0.28, numpy 0.23→0.28 for Python 3.14 compat
  - Adapted: Python module gated behind `python` feature flag for clean `cargo test`
- **Plan B (Data + Features)**: Full data layer implemented
  - Config: YAML + Pydantic validation
  - Data schemas for 9 types (OHLCV, funding, OI, depth, trades, liquidations, cross-exchange, FX, records)
  - Validation: monotonic timestamps, OHLC relationships, gap detection
  - Import: CSV/Parquet ingestion with auto-validation
  - Features: 7 indicators (SMA, EMA, RSI, Bollinger, VWAP, premium, funding_ma)
  - Feature builder: multi-source join + indicator computation from YAML config
- **Plan C (Strategy + Backtest + Report)**: Full strategy and orchestration layer
  - Strategy protocol (vectorized compute → signals DataFrame)
  - Momentum strategy (SMA crossover)
  - Mean revert strategy (Bollinger band bounce with state tracking)
  - Backtest orchestrator: config → strategy → Rust engine → save artifacts
  - Report: JSON metrics + manifest for any run
- **Plan D (Experiment + Optimize)**: Full experiment infrastructure
  - Leaderboard: atomic JSON read/write
  - Manifest: reproducibility metadata (data hash, git commit, versions)
  - Grid search + random search optimizers
  - goals.md template for Claude Code experiment loop
- **CLI**: `bt data/features/backtest/report/optimize/strategies` all wired
- **Tests**: 23 Rust + 21 Python = 44 total tests passing
- **Configs**: default.yaml, momentum_v1.yaml, goals.md

### Not Yet Done
- No real market data fetched (data sources need exchange-adapters integration)
- `bt data fetch` not implemented (needs exchange-adapters as dependency)
- No HTML report output (JSON only for now)
- No Claude Code `/loop` experiment session run yet
- Plans B/C/D not written as formal plan docs (executed directly from spec)

## Resume Here
1. **Get real data**: Integrate exchange-adapters, run `bt data fetch --pair BTC-USDT --source binance --type ohlcv --timeframe 1h`
2. **Build features**: Create `configs/features.yaml`, run `bt features build --config configs/features.yaml`
3. **Run first real backtest**: `bt backtest --config configs/strategies/momentum_v1.yaml`
4. **Start experiment loop**: Use Claude Code `/loop` with `experiments/goals.md`

## Decisions
- **Hybrid Rust+Python**: Rust owns simulation loop (speed), Python owns CLI/data/ML (ecosystem). No Python in hot loop.
- **Vectorized strategies**: Python precomputes signal columns, passes NumPy arrays to Rust once. No per-bar callbacks.
- **NumPy not Arrow for MVP**: PyO3 + numpy crate. Arrow zero-copy deferred.
- **Single instrument, long-only MVP**: No shorts, no multi-pair, no arb. Deferred to post-MVP.
- **leaderboard.json not SQLite**: Single source of truth for experiments. SQLite deferred.
- **`tick()` is shared state transition**: Both batch `run()` and future Phase 4 PPO `step()` call the same `tick()`.
- **Config is single source of truth**: `bt backtest --config X.yaml`. No `--strategy` flag.
- **PyO3 0.28 + Python feature gate**: Upgraded from plan's 0.23 for Python 3.14 compat; gated behind `python` cargo feature so `cargo test` works without linking libpython.

## Gotchas
- `exchange-adapters` (sibling dir) is Python async with httpx+pydantic — use as editable install via uv
- Drawdown must be checked AFTER mark-to-market (was a spec bug, now fixed)
- Emergency halt liquidation uses Immediate fill even if config says NextBar (intentional)
- Polars `pl.lit()` is an expression, not a Series — strategy examples must use `pl.Series()` for same-length columns
- Signal strength >= threshold means long, < threshold means flat (not >)
- `apply_fills` must clamp buy quantity if cash would go negative
- Python 3.14 requires PyO3 >= 0.28 (0.23 max supported was 3.13)
- `cargo test` requires the `python` feature to be OFF (default) — PyO3 cdylib can't link without Python runtime

## Context
- **Branch**: main (2 commits)
- **Tests**: 23 Rust + 21 Python = 44 total, all passing
- **Spec**: `docs/specs/2026-04-13-backtest-system-design.md`
- **Plan A**: `docs/plans/2026-04-13-plan-a-rust-engine.md`
- **Tech stack**: Rust (PyO3 0.28 + maturin), Python 3.14 (Polars, Typer, Pydantic, NumPy), uv
