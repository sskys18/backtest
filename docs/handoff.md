# Session Handoff
> Generated: 2026-06-04 19:10 (KST) — spec re-scope → general quant engine + self-review + Codex review, all fixed

## Task
**General multi-asset quant backtest engine** — backtest any cross-sectional / long-short /
portfolio strategy across **both intraday and mid-term (daily)** horizons. KR-perp long-short
on HL `xyz` perp = *first reference strategy*, not the definition. Spec =
`docs/specs/2026-06-04-multi-asset-quant-engine-design.md`. **Spec is awaiting user sign-off.**

## Status
### Completed — this session
- **Re-scoped spec** narrow→general (commit `38d72a2`): KR-perp demoted to first reference
  strategy; dual-horizon gated by `bar_freq`/`session_aware`; sector agents = forward-only
  thesis lane (§13). File renamed `kr-perp-portfolio-engine-design.md` →
  `multi-asset-quant-engine-design.md`. Self-review fixed 11 findings (F1–F9 + 2 second-pass).
- **Codex spec-review applied** (commit `21c103a`): Codex read the Rust source, returned 10
  findings (2 blocker), all fixed — C1 mechanical bias-artifact gate, C2 perp account
  invariants (`equity = collateral + realized + unrealized + funding − fees`), C3 funding mark
  `close[t−1]` not `close[t]`, C4 vectorized feature-lookahead guard + canary test, C5 PyO3
  boundary contract, C6 metrics-before-walk-forward, C7 hard/soft caps, C8 order priority,
  C9 daily calendar policy, C10 time-neutral wording.
- **Reference repos** saved: `docs/references/2026-06-04-engine-reference-repos.md` (borrow map:
  qlib signal / nautilus exec / skfolio CPCV / TradingAgents forward-only). Verified stars+dates.

### In Progress
- None. Spec complete + reviewed twice + committed. **Awaiting user sign-off.**

## Resume Here
1. **Get spec sign-off** → on "go", run `writing-plans` skill to generate the P0–P4 execution
   plan from spec §10. **No engine code before plan approval** (brainstorming HARD-GATE).
   Phase order: P0 bar-freq-aware panel loader/session-tagger/funding-reader (import-only) →
   P1 Rust `PortfolioEngine` + **2D PyO3 binding** (`python.rs` is 1D-only) → P2 `PanelStrategy`
   + 3 reference configs → **P2.5 metrics core** (C6, before walk-forward) → P3 walk-forward
   (purged CV + DSR/PBO) → P4 CLI `bt backtest-panel` + report.
2. Each phase ends green: `cargo test -p bt-engine` (28) + `pytest` (64). Crypto/PPO paths
   must stay untouched.

## Decisions (do NOT revisit)
- **General quant backtester, not KR-perp-specific** (user-locked this session). KR-perp =
  first reference strategy. Engine is universe/horizon-agnostic.
- **Dual-horizon, both first-class** — intraday (session-aware, perp funding, exposure-hour
  Sharpe) + mid-term daily (252/365 annualization). Gated by `bar_freq`/`session_aware`.
- **Sector-analysis agents = forward-only thesis lane** (§13), NEVER backtested. Backtesting
  LLM agents on recent KR data = training-leakage = fake alpha; mid-term makes it worse.
  Engine `bias[t]` seam is mechanically gated (C1) — fold-local PIT artifact only.
- **Approach A** (LOCKED): panel-vectorized signal `(T×N)` → signed `W∈[−1,+1]` → event-driven
  portfolio exec. B/C rejected. **extend bt-engine = ADDITIVE** — `portfolio_engine.rs` +
  `run_panel()` new; `tick()`/`run()`/`StepEngine`(PPO) unchanged.
- **Perp accounting = NEW path, not spot special case** — signed qty + collateral/margin +
  funding, independent of the spot/cash `run()`. Account invariants now in §5 (C2).
- **Data = import-only** — siblings (korea-stock/upbit) fetch; here `bt data import` only.

## Gotchas
- **Codex bridge flaked twice** before succeeding — first two dispatches died silently (printed
  `prompt OK`, no session, empty `codex.log`). Third wrote durable `.codex-review-findings.txt`
  (now gitignored via `.codex-review-*.txt` + removed). If re-running Codex, use a durable
  output path, not a self-deleting tmpdir.
- **PyO3 currently 1D-only** (`crates/bt-engine/src/python.rs`) — 2D `run_panel()` binding is
  net-new in P1, gates P2 strategy integration tests.
- **The vectorized-lookahead trap (C4)** is the subtle killer — `compute(panel)` sees the whole
  `(T×N)`, so next-bar fill stops *execution* lookahead but not *feature* lookahead. Spec §4
  mandates trailing-only helpers + a perfect-foresight canary test. Honor it in P2.
- **Thin data** = reference-strategy risk, not engine: HL `xyz` 6 assets × 105 days → DSR/PBO
  mandatory. Engine itself is universe-agnostic (richer KR-cash daily panels give more room).
- **Commit chokepoint**: use `git -C <path> commit`, never `cd path; git commit` (hook blocks
  the `;`). Bit this session.

## Context
- **Branch**: main, origin = github.com/sskys18/backtest (private). HEAD **`21c103a`**.
  **Not pushed** — local only (no push without explicit ask).
- **Tests**: not run this session (docs-only changes). Last known green: cargo bt-engine 28/28,
  pytest 64/64. venv `.venv` (py3.14), `.so` built via `maturin develop`.
- **Unknowns** (verify before acting): HL `xyz` liquidity/longevity (OOS sample thin); KIS
  minute semantics; **panel parquet real schema** — confirm against sibling output before P0
  loader; whether the optional golden-parity test (N=1 ↔ spot `run()`) is worth building.
