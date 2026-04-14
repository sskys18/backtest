# bt — Crypto Backtesting Framework

Hybrid Rust+Python backtesting system. Rust owns the simulation loop (speed), Python owns CLI/data/ML (ecosystem).

## Build & Test

```bash
# Rust tests (no Python needed)
cargo test -p bt-engine

# Python setup + build extension
uv venv && source .venv/bin/activate
maturin develop

# Python tests
uv run pytest tests/python/ -v

# CLI
uv run bt --help
uv run bt strategies
uv run bt backtest --config configs/strategies/momentum_v1.yaml
```

## Architecture

```
Python CLI (typer)  →  Strategy (polars, vectorized)  →  Rust Engine (PyO3/NumPy)
     ↓                        ↓                              ↓
  config.py              signals DataFrame              bar loop + fills
  backtest.py            strength + target_size         portfolio + risk
  report/                                               equity curve + metrics
```

- **No Python in the hot loop.** Strategies precompute signal arrays, Rust iterates bars.
- **Config is single source of truth.** `bt backtest --config X.yaml` — everything lives in YAML.
- **`tick()` is the shared state transition** — used by both batch `run()` and future PPO `step()`.

## Project Layout

- `crates/bt-engine/src/` — Rust engine (types, broker, portfolio, risk, tick, engine, python bindings)
- `src/bt/` — Python package (cli, config, data, features, strategy, backtest, report, experiment, optimize)
- `configs/` — YAML strategy configs
- `tests/python/` — Python tests
- `experiments/` — goals.md + leaderboard.json + run artifacts

## Key Conventions

- Rust crate uses feature `python` for PyO3 bindings (off by default for `cargo test`)
- PyO3 0.28 / numpy 0.28 required for Python 3.14
- Polars for all DataFrames, NumPy only at the Rust boundary
- Strategies must return `strength` (f64) and `target_size` (f64) columns, nulls filled with 0.0
- Signal strength >= threshold → long, < threshold → flat
- All data stored as Parquet, organized by type under `data/raw/`

## Data Types

OHLCV, funding, OI, depth, trades, liquidations, cross_exchange, fx, records — schemas in `src/bt/data/schemas.py`
