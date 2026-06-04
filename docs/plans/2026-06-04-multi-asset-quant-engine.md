# Implementation Plan — Multi-Asset Quant Backtest Engine

> Date: 2026-06-04 · Spec: `docs/specs/2026-06-04-multi-asset-quant-engine-design.md`
> Branch: `feat/multi-asset-quant-engine` · Approach A (locked)
> Execution: chained per-phase workflows (implement → adversarial-verify → build gate), one commit checkpoint per phase.

## Environment facts (load-bearing — every agent must obey)

- **Build the extension:** `source .venv/bin/activate && maturin develop` (py3.14, maturin 1.13.1). Compiles `crates/bt-engine` with `python` feature → installs `bt.bt_engine`.
- **Import name is `bt.bt_engine`**, NOT `bt_engine`. (`pyproject.toml` `module-name = "bt.bt_engine"`.)
- **Run pytest via `pytest.main`, never the bare `pytest` CLI** — a session hook munges the literal `pytest` command to "No tests collected". Use:
  ```bash
  source .venv/bin/activate && python -c "import pytest,sys; sys.exit(pytest.main(['tests/python/','-q']))"
  ```
- **Rust tests:** `cargo test -p bt-engine` (no Python needed). Baseline **28** `#[test]`.
- **Python baseline: 64 passed.** Both baselines must stay green after every phase.
- **Git chokepoints standalone** — `git -C <path> commit`, never `cd path; git commit`. Commit via `git commit -F <file>`.
- **Crypto/PPO paths are untouchable:** `tick()`, `run()`, `BacktestEngine`, `StepEngine`, `src/bt/replay/`, `src/bt/rl/`, `src/bt/scanners/`. Additive only.

## Current state (verified this session)

```
crates/bt-engine/src/  types.rs broker.rs portfolio.rs risk.rs tick.rs engine.rs step.rs python.rs lib.rs
src/bt/data/           import_.py schemas.py store.py validate.py  (import-only, no panel loader yet)
```
`python.rs` exposes `PyBacktestEngine` (1D `run`) + `PyStepEngine` (PPO). No 2D path. No `PanelStrategy`. No panel loader.

## Type contract (names fixed across all phases — do not rename)

Rust (`portfolio_engine.rs`):
```rust
pub struct PortfolioConfig {
    pub initial_collateral: f64,
    pub fee_rate: f64,            // taker, fraction
    pub slippage_bps: f64,
    pub im_rate: f64,             // initial margin rate
    pub mm_rate: f64,             // maintenance margin rate
    pub leverage_cap: f64,       // max gross Σ|W|
    pub max_net: f64,            // max |ΣW|
    pub max_per_asset: f64,      // max |W_a|
    pub rebalance_freq: usize,   // bars between rebalances; 1 = every bar
    pub fill_model: FillModel,   // NextBar (default) | Immediate
    pub session_aware: bool,
    pub on_violation: ViolationMode, // Reject (default) | Renormalize
    pub cap_kind: CapKind,       // Hard (default) | Soft{tolerance}
    pub numeraire_is_unit_fx: bool, // true → skip fx (single-currency)
}
pub enum FillModel { NextBar, Immediate }
pub enum ViolationMode { Reject, Renormalize }
pub enum CapKind { Hard, Soft { tolerance: f64 } } // Hard = force-reduce to bound every bar; Soft = drift within tolerance, correct at rebalance

pub struct PortfolioResult {
    pub equity: Vec<f64>,           // [T] in numeraire
    pub gross_exposure: Vec<f64>,   // [T]
    pub net_exposure: Vec<f64>,     // [T]
    pub funding_pnl: Vec<f64>,      // [T] per-bar funding cashflow
    pub turnover: Vec<f64>,         // [T]
    pub realized_pnl: Vec<f64>,     // [T] cumulative
    pub trades: Vec<TradeRec>,      // per-asset fills
}
// run_panel(assets, close, open, funding, fx, weights, active_mask, sessions, cfg,
//           bias=None, bias_available_at=None, bias_mode="forbidden") -> PortfolioResult
//   assets: list[str] — CANONICAL column order (C5). EVERY matrix's N columns map to this
//     list positionally; a caller-supplied per-matrix asset order that disagrees → reject.
//   all matrices row-major [T*N], plus T, N; column count must == len(assets).
//   bias (C1, optional [T,N]) + bias_available_at ([T]) — see P1.6: forbidden by default,
//     accepted only with available_at[t] <= decision_time[t] for all t. Scalar/missing
//     available_at → reject at ingestion.
```

Python:
```python
# src/bt/panel/loader.py
def load_panel(path: str, *, bar_freq: str, session_aware: bool,
               numeraire: str = "USD", calendar: str = "trading_days") -> "Panel": ...
class Panel:  # holds assets:list[str], timestamp[T], wide arrays open/high/low/close/volume/funding/fx/sector/session/active [T,N]
# src/bt/panel/strategy.py
class PanelStrategy(Protocol):
    def compute(self, panel: "PanelView") -> "WeightMatrix": ...  # W[T,N] in [-1,1]
```

---

## Phase P0 — Panel data layer (Python, import-only)

**Goal:** load an already-imported long parquet → aligned wide `(T,N)` arrays, with session tagging (intraday) and funding reader. No network. No Rust.

**Files:**
- `src/bt/panel/__init__.py` (new)
- `src/bt/panel/loader.py` (new) — long→wide pivot, alignment, active-mask, validation
- `src/bt/panel/session.py` (new) — KRX session tagger (UTC ms → KST session enum), no-op for daily
- `tests/python/test_panel_loader.py` (new)
- `tests/python/test_panel_session.py` (new)

**P0.1 — Panel schema + loader.** `load_panel` reads long parquet (cols per spec §3.1: `timestamp,asset,open,high,low,close,volume,funding_rate,session,sector`; `fx_rate` optional). Pivot to wide `[T,N]` arrays keyed by sorted unique `timestamp` (rows) × canonical `assets` (cols, sorted). **Emit all OHLCV arrays** `open/high/low/close/volume [T,N]` (not just open/close — strategies need high/low/volume features). Missing `(t,asset)` cells → `NaN` + `active_mask[t,a]=False`. `fx_rate` absent → ones (with `numeraire_is_unit_fx=True`). **Funding (C-rev finding):** a panel is perp-enabled or spot per config (`funding: true|false`). **Perp-enabled + `funding_rate` column absent → reject** (`PanelSchemaError`) — silent-zero funding would mask missing perp data as real zero funding (materially wrong returns). Spot (`funding: false`) + absent → zeros, allowed. Present column → used verbatim (zeros for non-perp rows are fine, they are real data). **Reject** (raise `PanelSchemaError`): missing required col; perp-enabled with no funding column; mixed-currency without `fx_rate` + declared `numeraire`; calendar-policy mix (§3.3 C9 — every trading row must be `session==2` in trading_days mode, or full-calendar rows must include `0=closed`).

**P0.2 — Active-mask + alignment contract (C5).** Every returned matrix shares identical `(T,N)` shape + column order. A cell that is NaN/inactive must not later carry weight (validated at engine ingestion, P1 — but the loader emits the mask). Universe drift: an asset absent for part of window is inactive there, never silently zero.

**P0.3 — Session tagger (`session.py`).** Intraday `session_aware=True`: map each UTC-ms timestamp → KST (UTC+9) → `session: u8` {0 closed,1 premarket,2 open,3 mid,4 close,5 overnight} using KRX hours (09:00–15:30 KST, premarket 08:00–09:00, close 15:20–15:30). Holidays: accept a holiday set arg (default empty → flag as TODO-curated, do not fabricate). Daily `session_aware=False`: no-op, every active row stays `2`.

**P0 tests (write first — TDD):**
- `test_load_long_to_wide_shapes` — 3 assets × 5 ts (one asset missing 1 ts) → all OHLCV arrays `[5,3]`, the missing cell NaN + `active_mask` False there.
- `test_ohlcv_arrays_present` — loaded Panel exposes `open/high/low/close/volume` all shape `[T,N]`, values match source.
- `test_funding_absent_spot_zeros` — `funding: false` + no `funding_rate` col → funding array all 0, no error.
- `test_funding_absent_perp_rejects` — `funding: true` + no `funding_rate` col → `PanelSchemaError` (missing perp funding not silently zeroed).
- `test_missing_required_col_raises` — drop `close` → `PanelSchemaError`.
- `test_mixed_currency_requires_fx` — two `sector`/currency assets, no `fx_rate`, numeraire set → raises.
- `test_calendar_policy_mix_rejected` — rows mixing `2` and `0` in trading_days mode → raises.
- `test_session_tagger_kst_open` — UTC ts at 00:30Z (=09:30 KST) → `session==2` open; 23:00Z prev (=08:00 KST) → premarket; daily mode → unchanged.

**P0 build gate:** `python -c "import pytest,sys; sys.exit(pytest.main(['tests/python/','-q']))"` → 64 + new pass. `cargo test -p bt-engine` → 28 (untouched).

**P0 adversarial-verify:** independent agent confirms — (a) no network/fetch import added; (b) NaN cells never coerced to 0 weight silently; (c) calendar policy genuinely rejects mixes (not just warns); (d) session math is UTC→KST correct at DST-free KST (KST has no DST — constant +9).

**P0 commit:** `feat(panel): bar-freq-aware panel loader + KRX session tagger (P0)`

---

## Phase P1 — Rust PortfolioEngine + 2D PyO3 binding

**Goal:** signed-position perp portfolio engine with exact account invariants, strict per-bar order, 2D PyO3 binding. This is the correctness crux (C2, C3, C8).

**Files:**
- `crates/bt-engine/src/portfolio_engine.rs` (new) — engine + `PortfolioConfig`/`PortfolioResult`/`TradeRec`/enums
- `crates/bt-engine/src/lib.rs` (edit — add `pub mod portfolio_engine;`)
- `crates/bt-engine/src/python.rs` (edit — ADD `run_panel` pyfn + register; do not touch existing classes)
- (tests inline `#[cfg(test)]` in `portfolio_engine.rs`)
- `tests/python/test_run_panel_binding.py` (new) — PyO3 2D smoke

**P1.1 — Account model (C2, exact).** State: `collateral, positions[N] (signed qty), entry_price[N], realized_pnl_cum, funding_paid_cum, fees_cum, turnover_cum, peak_equity`. Invariant held every bar:
`equity = collateral + realized_pnl + unrealized_pnl + funding_pnl − fees`, where `unrealized_pnl_a = qty_a·(mark_a − entry_a)` (signed). A fill moves **margin** (`|Δqty·price|·im_rate`) in/out of collateral + books fees + realized PnL on closed portion — **never full notional through cash**; a short open does **not** credit proceeds.

**P1.2 — Strict per-bar order (spec §5 steps 1–8).** Implement exactly:
1. Funding on **carried t−1 book**, mark = `close[t−1]` (C3, never `close[t]`); no-op if funding≡0; new positions not charged.
2. Fill `t−1` orders at `open[t]+slippage` (NextBar) — taker fee, update signed qty + margin + realized.
3. MTM at `close[t]` → equity, unrealized, peak.
4. Target notional `= equity·W[t,a]` — **only on rebalance bar** (`t % rebalance_freq == 0`); else skip (drift).
5. Rebalance diff → orders enqueued for `t+1` (Immediate fills `close[t]`, ablation only).
6. Margin/maintenance check vs equity → breach: force-reduce toward bound (enqueue t+1). Wipeout → flat+halt.
7. Risk gates: hard caps → force-reduce every bar; soft caps → drift within `cap_tolerance`, correct next rebalance; new exposure halted on breach.
8. Session force-flat (intraday `session_aware` only).

**P1.3 — Order priority & netting (C8).** Per-asset single net pending order. Priority: `liquidation/dd-halt > session-flat > margin-reduce > hard-cap-reduce > rebalance`. Before enqueue, cancel/net any lower-priority pending for same asset.

**P1.4 — Weight validation at ingestion (C5, §4).** Reject (default `on_violation=Reject`, raise with `(t,asset,value)`): `|W[t,a]|>max_per_asset`, `gross=Σ|W|>leverage_cap`, `|net|>max_net`, or an active-mask violation (NaN/inactive cell with nonzero W → **hard-fail**). `Renormalize` scales row to binding cap + logs. **Never silent clamp.**

**P1.5 — 2D PyO3 binding.** Add `run_panel(py, assets, close, open, funding, fx, weights, active_mask, sessions, cfg_dict, bias=None, bias_available_at=None, bias_mode="forbidden")` taking `assets: Vec<String>` + `PyReadonlyArray2<f64>` matrices (+ `active_mask` as `PyReadonlyArray2<bool>` or f64 0/1, `sessions` as u8/i64 2D). **Alignment validation (C5, finding #1):** every matrix's column count must equal `len(assets)`; reject (raise) on `N` mismatch across matrices or `N != len(assets)`. `assets` is the canonical positional order — there is no per-matrix asset list to disagree, so the contract is "all matrices share the same N and the caller guarantees column k = assets[k]"; the loader (P0) is the single producer enforcing this, and the binding rejects shape drift. Returns dict of arrays (equity, gross, net, funding_pnl, turnover, realized, trades). Register in `bt_engine` module fn. **Do not modify `PyBacktestEngine`/`PyStepEngine`.**

**P1.6 — Bias point-in-time ingestion gate (C1 structural seam, finding #2).** The leak-proof gate lives at `run_panel` ingestion — the boundary EVERY caller hits — not only in P3's Python harness (a direct `run_panel(bias=...)` call must not bypass it). At ingestion: `bias_mode` defaults to `"forbidden"` → any non-None `bias` is **rejected**. `bias_mode ∈ {"backtest","forward"}` requires `bias_available_at` present (length T) and **every `available_at[t] ≤ decision_time[t]`** else reject; a scalar/window-constant bias (no per-`t` availability) → reject; `bias` shape must be `[T,N]` aligned. (The higher-level checks — `source_run_id` resolves to a **fold-local** manifest, purge/embargo — are built in P3's `bias.py` which constructs the artifact and calls `run_panel` with the validated matrix; P1 owns the structural inequality + default-forbidden seam.)

**P1 Rust tests (write first):**
- `funding_accrual_on_carried_book` — open long at t0, funding at t1 charges on t0 notional marked `close[t0]`; a position opened at t1 is NOT charged at t1.
- `funding_mark_uses_prev_close` — assert funding uses `close[t−1]`, regression vs `close[t]`.
- `short_pnl_sign` — short qty, mark falls → unrealized positive; mark rises → negative.
- `equity_invariant_holds_each_bar` — random small scenario, assert `equity ≈ collateral+realized+unrealized+funding−fees` every bar (1e-9).
- `short_open_no_cash_credit` — opening a short does not increase collateral by notional (only margin moves).
- `rebalance_diff_enqueues_next_bar` — order at t fills at `open[t+1]`, not `close[t]`.
- `rebalance_freq_drift` — `rebalance_freq=3`: non-rebalance bars don't re-trade; positions drift.
- `margin_breach_force_reduces` — push equity < maintenance → de-risk orders enqueued; wipeout → flat+halt.
- `hard_cap_reduces_every_bar` — drift pushes gross over hard cap between rebalances → force-reduced same bar.
- `weight_violation_rejected` — `Σ|W|>leverage_cap` → error with (t,asset,value); active-mask NaN+nonzero-W → hard-fail.
- **C8 full priority matrix (finding #4)** — separate assertions, not just one pair:
  - `priority_liquidation_over_all` — liquidation/dd-halt order beats a same-asset session-flat, margin-reduce, hard-cap-reduce, and rebalance pending (each tested).
  - `priority_session_flat_over_lower` — session-flat beats margin-reduce/hard-cap/rebalance.
  - `priority_margin_over_cap_and_rebalance` — margin-reduce beats hard-cap-reduce and rebalance.
  - `priority_hardcap_over_rebalance` — hard-cap-reduce beats rebalance.
  - `pending_order_netting_same_asset` — a new lower-priority order for an asset with a higher-priority pending is cancelled/netted, not appended (single net pending per asset).
- **C2 mixed-currency fx (finding #6)** — `equity_invariant_mixed_currency`: 2 assets, one non-numeraire with a **changing `fx_rate[t]`**; hand-calc that collateral, margin, unrealized, realized, fees, and funding all convert via `fx_rate` of the correct PIT timestamp through open → partial-close → short → funding → MTM; equity invariant holds in numeraire each bar (1e-9). `numeraire_is_unit_fx=true` path (fx≡1) gives identical result to skipping conversion.
- **CapKind (finding #9)** — `hard_cap_force_reduce` (drift over hard cap → reduced same bar) vs `soft_cap_drifts_within_tolerance` (drift within `Soft{tolerance}` → logged, not reduced until rebalance; beyond tolerance → reduced).
- **Bias ingestion (C1, finding #2/#5)** — `bias_forbidden_by_default` (non-None bias + default mode → reject); `bias_future_available_at_rejected` (`available_at[t] > decision_time[t]` → reject); `bias_scalar_rejected` / `bias_missing_available_at_rejected`; `bias_valid_pit_accepted` (all `available_at[t] ≤ decision_time[t]` → runs, tilt applied).
- **Alignment (C5, finding #1)** — `run_panel_rejects_N_mismatch` (a matrix with `N != len(assets)` → reject); `run_panel_rejects_col_count_drift` (two matrices with different N → reject).

**P1 Python test:** `test_run_panel_binding.py` — build tiny `(T=4,N=2)` arrays + `assets=["A","B"]`, call `bt.bt_engine.run_panel(...)`, assert returns dict with `equity` length 4, shapes consistent; a wrong-length `assets` → raises.

**P1 build gate:** `cargo test -p bt-engine` → 28 + new (~11) pass. `maturin develop` succeeds. `pytest` → 64 + P0 + binding pass.

**P1 adversarial-verify (CRITICAL):** independent agent re-derives by hand on a 3-bar scenario: (a) the equity invariant bar-by-bar; (b) funding mark is `close[t−1]`; (c) short PnL sign; (d) no full-notional cash movement on open; (e) order-priority cannot let a rebalance override a force-flat (test the full ladder, not one pair); (f) a mixed-currency bar with changing `fx_rate` keeps the invariant in numeraire; (g) attempt a direct `run_panel(bias=<hindsight>)` call (future `available_at`, scalar) and confirm the ingestion seam rejects it (not deferred to P3). Agent reports PASS/FAIL per invariant with the arithmetic, not "tests pass".

**P1 commit:** `feat(engine): additive PortfolioEngine + 2D run_panel PyO3 binding (P1)`

---

## Phase P2 — PanelStrategy API + primitives + reference configs

**Goal:** Python strategy layer producing validated `W[T,N]`, two primitives, three reference configs, integration-tested against P1 binding.

**Files:**
- `src/bt/panel/strategy.py` (new) — `PanelStrategy` Protocol, `PanelView`, `WeightMatrix`, trailing-only helpers
- `src/bt/panel/primitives.py` (new) — `CrossSectionalStrategy` (§4.1), `PairsStrategy` (§4.2)
- `src/bt/panel/run.py` (new) — wire loader → strategy → `run_panel` → result
- `configs/panel/kr_perp_pairs_intraday.yaml`, `configs/panel/kr_cs_rotation_intraday.yaml`, `configs/panel/kr_sector_rotation_daily.yaml` (new)
- `tests/python/test_panel_strategy.py`, `tests/python/test_panel_primitives.py`, `tests/python/test_panel_lookahead_canary.py` (new)

**P2.1 — `PanelView` + trailing-only helpers (C4).** Exposes aligned arrays + `session/sector/funding`. Helpers `rank`, `sector_demean`, `vol_scale`, `rolling` are **trailing-only by default**; any forward transform requires explicit `lag`/`as_of` metadata or is rejected.

**C4 enforcement limit (finding #3 — honest scope).** Approach A is *locked* on a vectorized `compute(panel)` that sees the whole `(T×N)` for speed; a hard sandbox (per-`t` sliced PanelView) would defeat that and is explicitly out of scope. So the guard is **defense-in-depth, not a sandbox**: (1) trailing-only helpers cover the sanctioned path; (2) raw array access is the escape hatch a determined `panel.close[t+1]` can still abuse — this is caught by the **canary battery** (P2 tests + adversarial-verify), not prevented mechanically; (3) the optional `as_of`/provenance tracking on helper outputs flags forward transforms that route through helpers. **Document this limit in `strategy.py` docstring** so a future strategy author knows raw indexing is on the honor system + canary, and note provenance-tracked PanelView as a future hardening option (not built now — would not change the locked vectorized API).

**P2.2 — `CrossSectionalStrategy` (§4.1 fixed order):** score → rank → top-k/bottom-k → raw weight (equal/score-prop/inverse-vol) → optional sector-neutralize (demean within sector) → vol-scale → cap/renormalize → optional time/regime gate (applied last, zeroes weights when closed).

**P2.3 — `PairsStrategy` (§4.2):** dollar-neutral leg pairs on spread z-score, `W` gross-balanced ~net 0.

**P2.4 — Three reference configs** exercising both horizons: intraday pairs (KR-perp), intraday CS rotation, **daily sector-rotation** (a `CrossSectionalStrategy` instance — proves daily path; `session_aware:false`, `periods_per_year:252`).

**P2 tests (write first):**
- `test_cs_rotation_weights_valid` — synthetic 6-asset panel → W respects `Σ|W|≤leverage_cap`, top-k long / bottom-k short.
- `test_sector_demean_neutral` — within-sector weights sum ~0 after demean.
- `test_pairs_net_neutral` — pairs W net ~0.
- `test_trailing_helper_no_lookahead` — `rank`/`rolling` at t uses only ≤t rows.
- **C4 canary battery (finding #3, critical)** — a perfect-foresight scorer must be caught for EACH cheat form (separate cases): `test_canary_future_shift` (`close[t+1]` / `shift(-k)`), `test_canary_centered_window` (centered rolling), `test_canary_future_max` (`max(close[t:])`), `test_canary_full_sample_zscore` (full-sample z-score without `as_of`). The canary = run the cheater, confirm the guard/detector flags it OR (for raw-array cheats the guard can't see) confirm the perfect-foresight equity-vs-OOS divergence detector trips. A canary that lets any cheater through silently is itself a failure.
- `test_end_to_end_daily_rotation` — load synthetic daily panel → CS rotation → `run_panel` → equity curve length matches, no exception.

**P2 build gate:** `cargo test` 28+ · `pytest` 64 + P0 + P1 + P2 green.

**P2 adversarial-verify:** independent agent writes its OWN cheating strategy (centered window, `shift(-2)`, future-max) and confirms the guard flags each. Confirms the daily config truly runs the daily path (`session_aware:false`, 252 annualization wired). Reports which cheats were caught.

**P2 commit:** `feat(panel): PanelStrategy API + CS/Pairs primitives + 3 reference configs (P2)`

---

## Phase P2.5 — Metrics core (before walk-forward, C6)

**Goal:** Sharpe/Sortino/max_dd/turnover/funding/exposure + mode-gated annualization (§8). DSR/PBO will consume this in P3, so it must exist first.

**Files:**
- `src/bt/panel/metrics.py` (new)
- `tests/python/test_panel_metrics.py` (new)

**P2.5.1 — Core metrics:** from `PortfolioResult` compute Sharpe, Sortino, max_dd, turnover, funding_pnl total, gross/net exposure series, hit rate, **long/short attribution**.

**P2.5.2 — Mode-gated annualization (§8):**
- Daily (`session_aware=False`): `μ/σ · sqrt(periods_per_year)` (252 KR-cash / 365 crypto).
- Intraday (`session_aware=True`): additionally **time-neutral Sharpe** — `r_t=Δequity_t/equity_{t−1}`, `h_t=Δt_bar_hours·1[gross_t>0]`; `μ_h=Σ(r_t·h_t)/Σh_t`, `σ_h=sqrt(Σh_t(r_t−μ_h)²/Σh_t)`; `A=HOURS_PER_YEAR/mean(h_t over exposed bars)`, Sharpe·sqrt(A). Report **exposure coverage** alongside. Flat bars stay in raw series; excluded from time-neutral by `h_t=0` (C10).

**P2.5 tests (write first):**
- `test_sharpe_daily_annualization` — known returns → Sharpe matches `μ/σ·sqrt(252)` to 1e-9.
- `test_max_dd` — crafted equity curve → exact max_dd.
- `test_time_neutral_sharpe` — intraday series with known `h_t` → `μ_h/σ_h·sqrt(A)` matches hand calc; flat bars excluded by weight.
- `test_exposure_coverage_reported` — coverage = Σ exposed hours / Σ wall-clock.
- `test_long_short_attribution` — sums to total PnL.

**P2.5 build gate + adversarial-verify** (hand-check one Sharpe + one time-neutral calc). **Commit:** `feat(panel): metrics core + mode-gated annualization (P2.5)`

---

## Phase P3 — Walk-forward / OOS harness (purged CV + DSR/PBO)

**Goal:** rolling/anchored folds, purge+embargo (horizon-scaled), per-fold tune-on-train/eval-on-test, DSR + BH-FDR + optional PBO. Includes the **bias point-in-time gate (C1)**.

**Files:**
- `src/bt/panel/walkforward.py` (new) — splits, purge/embargo, fold loop
- `src/bt/panel/stats.py` (new) — DSR, PBO, BH-FDR
- `src/bt/panel/bias.py` (new) — typed bias artifact + PIT gate (C1)
- `tests/python/test_walkforward.py`, `tests/python/test_dsr_pbo.py`, `tests/python/test_bias_gate.py` (new)

**P3.1 — Splits + purge/embargo (§7):** rolling/anchored by date; gap train↔test; **embargo scales with holding period** (config, not constant) — intraday hours, mid-term ≥N days.

**P3.2 — Fold loop:** tune params (reuse existing Optuna TPE in `src/bt/optimize/`) on train → freeze → eval on test → aggregate **OOS only**.

**P3.3 — DSR + PBO + BH-FDR (stats.py):** Deflated Sharpe (Bailey/López de Prado), BH-FDR across trial set, optional PBO.

**P3.4 — Bias PIT gate (C1, leak-proof) — builds ON the P1.6 ingestion seam.** `bias.py` constructs the typed artifact `{bias_values[T,N], available_at[T], source_run_id, generated_at, mode∈{backtest,forward}}` and the **higher-level** checks the Rust seam can't do: `source_run_id` resolves to a **fold-local** production manifest (bias generated using only that fold's train data) + passes the same purge+embargo as any feature. It then calls `run_panel(bias=..., bias_available_at=..., bias_mode=...)` — where the P1.6 seam re-enforces default-forbidden + `available_at[t]≤decision_time[t]` + no-scalar **mechanically at the boundary** (so a caller bypassing `bias.py` still can't smuggle). The two layers compose: structural inequality at P1.6, provenance/fold-locality/embargo at P3.

**P3 tests (write first):**
- `test_purge_embargo_no_overlap` — train/test label windows don't overlap after purge; embargo scales with holding period.
- `test_oos_only_aggregation` — in-sample metrics never leak into headline.
- `test_dsr_discounts_trials` — DSR < raw Sharpe as trial count rises; known input matches formula.
- `test_pbo_basic` — crafted overfit case → high PBO.
- **C1 bias gate battery (finding #5)** — separate cases: `test_bias_future_available_at_rejected`; `test_bias_missing_available_at_rejected`; `test_bias_untyped_rejected` (raw array, no artifact wrapper); `test_bias_default_forbidden_in_backtest` (mode unset → reject); `test_bias_scalar_rejected`; `test_bias_window_constant_rejected`; `test_bias_non_fold_local_source_run_id_rejected` (manifest from a different/whole-sample fold → reject); `test_bias_fails_purge_embargo_rejected`; `test_bias_valid_fold_local_pit_accepted` (all gates pass → tilt applied).

**P3 build gate + adversarial-verify** — independent agent attempts to smuggle a hindsight bias (future `available_at`, scalar "current view") and confirms each is rejected; confirms OOS aggregation excludes in-sample. **Commit:** `feat(panel): walk-forward OOS harness + DSR/PBO + bias PIT gate (P3)`

---

## Phase P4 — CLI + report

**Goal:** `bt backtest-panel` command + report presentation (metrics already exist from P2.5).

**Files:**
- `src/bt/cli.py` (edit — ADD `backtest-panel` subcommand; do not touch `backtest`/`replay`/others)
- `src/bt/panel/report.py` (new) — format/export metrics
- `tests/python/test_cli_backtest_panel.py` (new)

**P4.1 — CLI:** `bt backtest-panel --panel <panel.parquet> --config <portfolio.yaml>`. Config keys (§9): `universe, bar_freq, rebalance_freq, session_aware, numeraire, periods_per_year, leverage_cap, max_gross/net, on_violation, session_rules, funding, walk_forward:{scheme,folds,embargo}`. `bt backtest` unchanged.

**P4.2 — Report:** OOS Sharpe distribution, DSR, PBO, per-fold stability, long/short attribution, funding, exposure, turnover. Export (json + console table).

**P4 tests (write first):**
- `test_cli_backtest_panel_smoke` — synthetic panel + config → runs end-to-end, emits report dict.
- `test_cli_backtest_unchanged` — existing `bt backtest` still parses/works.

**P4 build gate + adversarial-verify** — confirm `bt backtest`/`bt replay`/`bt strategies` unaffected; full suite green. **Commit:** `feat(cli): bt backtest-panel command + panel report (P4)`

---

## Final gate (after P4)

- `cargo test -p bt-engine` → 28 + P1 additions, all green.
- `pytest tests/python/` → 64 + all new, all green.
- `bt backtest --help`, `bt replay --help`, `bt strategies` unaffected.
- Crypto/PPO/replay/scanner paths byte-untouched (diff review).
- Update `docs/handoff.md`. Open PR `feat/multi-asset-quant-engine` → main (user confirm before merge).

## Spec coverage map

| Spec § | Phase |
|---|---|
| §3.1 panel / §3.3 sessions / §3.4 fx | P0 |
| §5 portfolio engine / C2/C3/C8 / §4 weight-validation / C5 assets-list / C1 bias ingestion seam | P1 |
| §4 strategy API / §4.1 CS / §4.2 pairs / C4 lookahead | P2 |
| §8 metrics + annualization / C6 / C10 | P2.5 |
| §7 walk-forward / DSR/PBO / §13 bias provenance+fold-locality C1 (on P1.6 seam) | P3 |
| §9 CLI / report | P4 |
| §13 thesis lane (forward-only) | OUT of P0–P4 (later track) |
