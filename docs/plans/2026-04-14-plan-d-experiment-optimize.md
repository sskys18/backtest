# Plan D: Experiment Tracking + Optimization

**Spec:** `docs/specs/2026-04-13-backtest-system-design.md` (sections 9, 7.3)
**Scope:** Leaderboard, manifest, grid/random optimizer, CLI wiring, goals.md
**Depends on:** Plan C (backtest orchestrator)
**Status:** Completed 2026-04-14

---

## Work Stream 1: Experiment Tracking

### Files
- `src/bt/experiment/leaderboard.py` — atomic read/write of `experiments/leaderboard.json`
- `src/bt/experiment/manifest.py` — `compute_file_hash()` utility

### Leaderboard Schema
```json
{
  "best_run_id": "2026-04-13_007",
  "best_sharpe": 1.42,
  "best_metrics": { ... },
  "total_runs": 12,
  "feasible_runs": 8,
  "history": [{"run_id": "...", "sharpe": 0.85, "feasible": true}]
}
```

### Atomic Updates
- Write to `.json.tmp` then `os.rename()` (POSIX atomic)
- `update_leaderboard()` tracks feasibility + best Sharpe

---

## Work Stream 2: Optimizer

### Files
- `src/bt/optimize/base.py` — Optimizer protocol
- `src/bt/optimize/grid.py` — GridSearchOptimizer + RandomSearchOptimizer
- `src/bt/optimize/run.py` — `run_optimize()` dispatch

### Search Space Format (YAML)
```yaml
strategy.params.fast_period: {min: 5, max: 50, step: 5}
strategy.params.position_size: {values: [0.05, 0.10, 0.15, 0.20]}
risk.signal_threshold: {min: 0.3, max: 0.8, step: 0.1}
```

### Grid Search
- Expands cartesian product of all ranges
- Caps at budget
- Each combo: writes temp YAML → runs `run_backtest()` → captures Sharpe

### Random Search
- Samples uniformly from ranges
- Budget-limited iterations

---

## Work Stream 3: CLI Wiring

### File
- `src/bt/cli.py` — Typer dispatcher

### Commands
| Command | Handler |
|---------|---------|
| `bt data validate --path` | `bt.data.validate.validate_path()` |
| `bt data import --path --type` | `bt.data.import_.import_file()` |
| `bt features --config` | `bt.features.build.build_features()` |
| `bt backtest --config` | `bt.backtest.run_backtest()` |
| `bt report --run-id` | `bt.report.generate.generate_report()` |
| `bt optimize --config --space --method --budget` | `bt.optimize.run.run_optimize()` |
| `bt strategies` | Lists registry entries |

All commands output JSON to stdout (parseable by Claude Code experiment loop).

---

## Work Stream 4: Default Configs

### Files
- `configs/default.yaml` — base config template
- `configs/strategies/momentum_v1.yaml` — ready-to-use momentum config
- `experiments/goals.md` — Claude Code experiment loop contract

### goals.md
Defines: objective (max Sharpe), constraints (DD < 15%, trades >= 50), allowed mutations (YAML only), parameter bounds, fixed config, budget (50 experiments).

---

## Claude Code Experiment Loop (Future)
The system is wired for autonomous experimentation:
1. Claude Code reads `goals.md`
2. Reads `leaderboard.json` for past results
3. Generates config YAML within bounds
4. Runs `bt backtest --config <path>`
5. Parses metrics JSON from stdout
6. Updates leaderboard if feasible + better
7. Repeats until budget exhausted

Not yet run — requires real market data first.
