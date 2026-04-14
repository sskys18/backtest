# Session Handoff
> Generated: 2026-04-14 00:15

## Task
Build a hybrid Rust+Python crypto backtesting framework (`bt` CLI) with autonomous Claude Code experiment loop.

## Status
### Completed
- Full design spec: `docs/specs/2026-04-13-backtest-system-design.md` (~1100 lines, reviewed 3x by Codex, all blockers fixed)
- Implementation Plan A (Rust engine): `docs/plans/2026-04-13-plan-a-rust-engine.md` — 7 work streams, 16 tasks, complete code for all Rust files
- Explored 5 existing projects (binance-scraper, exchange-adapters, kimp-simulator, kimchi-arbitrage, gaekju) for reusable patterns
- Memory saved: user profile + project vision in `.claude/projects/-Users-sskys-Mine-backtest/memory/`

### In Progress
- **Plan A not yet executed** — no code written yet, project dir has only docs/
- Plans B (Data+Features), C (Strategy+Backtest+Report), D (Experiment+Optimize) not yet written

## Resume Here
1. Execute Plan A: `docs/plans/2026-04-13-plan-a-rust-engine.md` — start with Task 1.1 (git init + scaffold), then work streams 2-7
2. Recommended execution: use `sspower:subagent-driven-development` — WS2-5 (types, broker, portfolio, risk) are independent and can run in parallel agents
3. After Plan A passes verification (22+ Rust tests, 3 Python integration tests), write and execute Plans B-D

## Decisions
- **Hybrid Rust+Python**: Rust owns simulation loop (speed), Python owns CLI/data/ML (ecosystem). No Python in hot loop.
- **Vectorized strategies**: Python precomputes signal columns, passes NumPy arrays to Rust once. No per-bar callbacks.
- **NumPy not Arrow for MVP**: PyO3 + numpy crate. Arrow zero-copy deferred.
- **Single instrument, long-only MVP**: No shorts, no multi-pair, no arb. Deferred to post-MVP.
- **leaderboard.json not SQLite**: Single source of truth for experiments. SQLite deferred.
- **`tick()` is shared state transition**: Both batch `run()` and future Phase 4 PPO `step()` call the same `tick()`.
- **Config is single source of truth**: `bt backtest --config X.yaml`. No `--strategy` flag. Strategy name, data path, all params in YAML.
- **Multi-data-type support**: OHLCV + funding rates + OI + order book + trades + liquidations + cross-exchange + FX + trading records. All joined by timestamp in feature builder.
- **Keep separate from kimp project**: kimp is real-time dashboard, backtest is historical batch. Different lifecycles.

## Gotchas
- `exchange-adapters` (sibling dir) is Python async with httpx+pydantic — use as editable install via uv
- Drawdown must be checked AFTER mark-to-market (was a spec bug, now fixed)
- Emergency halt liquidation uses Immediate fill even if config says NextBar (intentional)
- Polars `pl.lit()` is an expression, not a Series — strategy examples must use `pl.Series()` for same-length columns
- Signal strength >= threshold means long, < threshold means flat (not >)
- `apply_fills` must clamp buy quantity if cash would go negative

## Context
- **Branch**: no git yet (Task 1.1 initializes it)
- **Tests**: none yet (Plan A creates 22+ Rust + 3 Python tests)
- **Spec**: `docs/specs/2026-04-13-backtest-system-design.md`
- **Plan A**: `docs/plans/2026-04-13-plan-a-rust-engine.md`
- **Tech stack**: Rust (PyO3+maturin), Python (Polars, Typer, Pydantic, NumPy), uv
