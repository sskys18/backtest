# Session Handoff
> Generated: 2026-06-05 (KST) — engine P0–P4 merged to main (#1); docs aligned + 4 follow-ups (DSR/PBO wiring, hours_per_year, full_calendar, C4 hardening) done on feat/panel-followups

## Latest (panel follow-ups session)
- **P0–P4 merged to main** as squash `5e49b28` (PR #1). Working engine: `bt backtest-panel`.
- **Docs aligned**: CLAUDE.md + docs/architecture.md now describe the panel quant engine (3rd mode).
- **4 follow-ups done** on `feat/panel-followups` (each 3-lens/independently verified):
  - FU1 DSR/PBO wired end-to-end into the report (real Deflated-Sharpe probability + real CSCV PBO when C≥2, honest None when C<2; oos_returns per-fold concat, not equity-stitched).
  - FU2 `hours_per_year` config-driven (8760 perp / ~1638 KR-cash intraday).
  - FU3 loader `full_calendar` reject branch (C9 both directions).
  - FU4 `detect_lookahead` multi-perturbation (n=8): targeted-discrete catch ~80%→~100% at the auditing split; non-split-aligned cheats still slip (documented bound).
  - Gate: cargo **61**, pytest **160**. Additive; forbidden paths untouched.
- **GIT AUTH GOTCHA (important):** plain `git fetch`/`pull`/`push` over https resolve the WRONG GitHub account (`yuseongkim-inbl`) → "Repository not found". The repo is private to `sskys18`. Use the sskys18 token explicitly:
  `tok=$(gh auth token -u sskys18); git fetch "https://x-access-token:${tok}@github.com/sskys18/backtest.git" main`. `gh` write-pattern commands with `-R sskys18/backtest` are routed correctly by the identity hook; read-only `gh` (repo view / pr view) are NOT and fail — judge by the routed commands.
- **Workflow `args` bug:** the phase-runner `args` channel did NOT propagate to the script (every phase saw `phase=UNKNOWN`, empty focus). P0–P4 succeeded only because agents inferred the phase from the plan doc + git; FU work was done via DIRECT agents instead. If reusing `.claude/workflows/phase-runner.mjs`, pass scope through the plan doc or fix args propagation.

---


## Task
**General multi-asset quant backtest engine** — built per spec
`docs/specs/2026-06-04-multi-asset-quant-engine-design.md` and plan
`docs/plans/2026-06-04-multi-asset-quant-engine.md`. Approach A (panel-vectorized
signal → event-driven portfolio). KR-perp long-short = first reference strategy.

## Status
**P0–P4 COMPLETE.** Branch `feat/multi-asset-quant-engine`, 7 commits (plan + 6 phases).
Each phase: implement (TDD) → 3-lens adversarial verifier panel (arithmetic /
causality-leak / spec-conformance) → fix-loop → independent re-gate → commit.
**Local only — NOT pushed. Awaiting user decision on push/PR/merge.**

### Final gate (verified)
- `cargo test -p bt-engine` → **61 passed** (was 28; +33 portfolio_engine tests).
- `pytest tests/python/` → **151 passed** (was 64; +87 panel tests). Run via
  `python -c "import pytest,sys; sys.exit(pytest.main(['tests/python/','-q']))"`
  (bare `pytest` CLI is hook-munged — see plan env facts).
- Crypto/PPO/replay/scanners/`backtest.py`/`tick`/`run`/`engine`/`step` —
  **byte-untouched vs main** (`git diff --stat main...HEAD` on those = empty).
  `python.rs` additive (run_panel added; existing classes intact, 1 import-line swap).

### What shipped per phase
- **P0** `src/bt/panel/loader.py` + `session.py` — long→wide OHLCV panel, active-mask,
  C5 alignment, C9 calendar reject, perp-funding-required reject, KRX UTC→KST tagger.
- **P1** `crates/bt-engine/src/portfolio_engine.rs` + 2D `run_panel` PyO3 binding —
  signed-qty perp, C2 equity invariant, C3 funding `close[t-1]`, §5 per-bar order,
  C8 priority ladder, weight validation, CapKind Hard/Soft, C1 bias ingestion seam.
  *(Panel caught+fixed: dead Soft-cap, NaN bias, dead fill_model.)*
- **P2** `strategy.py` (PanelView trailing-only helpers + C4 docstring) + `primitives.py`
  (CrossSectional §4.1, Pairs §4.2) + `run.py` (detect_lookahead canary) + 3 configs.
- **P2.5** `metrics.py` — Sharpe/Sortino/max_dd/turnover/funding/exposure/hit/
  long-short attribution + mode-gated annualization (daily 252/365 vs intraday
  time-neutral, C10). *(Panel caught+fixed: fx-NaN attribution-vs-engine parity.)*
- **P3** `walkforward.py` (purged CV, horizon-scaled embargo, OOS-only) + `stats.py`
  (DSR/PSR/expected-max-Sharpe/BH-FDR/PBO, no-scipy) + `bias.py` (C1 provenance:
  BiasRunRegistry, mechanical run_id resolution). *(Panel caught+fixed a real C1
  leak: fabricated-manifest hindsight smuggle was being accepted; now run_id is
  resolved against a registry, registered bounds used, fingerprint tamper check.)*
- **P4** `cli.py` `backtest-panel` subcommand + `report.py` (orchestration + console).

## Resume Here
- **All four P0–P4 follow-ups are DONE** (feat/panel-followups, see "Latest" above) — the
  list below is the original P0–P4 gap log, now RESOLVED, kept for provenance:
  - ~~DSR/PBO not wired end-to-end~~ → **FU1 done**: report emits real Deflated-Sharpe
    probability + real CSCV PBO (honest None when C<2); threaded through walk-forward too.
  - ~~`hours_per_year` defaults 8760~~ → **FU2 done**: config-driven (single-pass + walk-forward).
  - ~~Loader `full_calendar` branch unvalidated~~ → **FU3 done**: rejects mislabeled/typo'd/
    no-session-column calendars (both directions).
  - ~~C4 only probabilistic for discrete~~ → **FU4 done**: multi-perturbation lifts catch to
    ~100% at the auditing split. Remaining honest bound: non-split-aligned discrete cheats
    can still slip (split placement unchanged) — documented in run.py, defense-in-depth canary.
- **Next:** `/ship feat/panel-followups → main` (push via the sskys18 token, see gotcha above).
  Nothing else pending.

## Decisions (locked, do NOT revisit)
- General quant backtester, KR-perp = first reference strategy. Dual-horizon.
- Approach A. bt-engine extension ADDITIVE. Perp = new path. Data import-only.
- §13 thesis-lane / DART agents = forward-only, OUT of P0–P4 (agents that drifted
  into DART event-study were caught and their artifacts removed).

## Context
- Branch `feat/multi-asset-quant-engine`, base `main`. HEAD `d293d13`. **Not pushed.**
- venv `.venv` (py3.14) now has pytest/optuna/gymnasium/stable-baselines3/PyJWT
  (uv.lock committed). `.so` built via `maturin develop`.
- Workflow runner: `.claude/workflows/phase-runner.mjs` (3-lens panel, agentSafe
  StructuredOutput-crash guard) — reusable for future phases.
