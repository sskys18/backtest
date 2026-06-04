# KR-Perp Portfolio Backtest Engine — Design

> Date: 2026-06-04 · Status: **Reviewed (self + Codex spec-review), awaiting user sign-off** · Approach: **A** (approved)
> Supersedes scope of: `docs/specs/2026-04-13-backtest-system-design.md` (single-asset long-only MVP)

## 1. Goal

A backtest engine for **KOSPI-major long-short strategies on perp** (Hyperliquid `xyz`
DEX: `xyz:SMSN`/`xyz:SKHX`/`xyz:HYUNDAI` legs vs `xyz:NVDA`/`xyz:MU`/`xyz:TSM` hedge
legs), plus **cross-sectional sector/stock rotation** ("after time/regime, buy certain
sector or names"). The current `bt-engine` cannot express any of it — long-only,
single-instrument, no funding, bar-agnostic time.

Non-goal: replacing the crypto OHLCV path or the `replay` microstructure engine. Those
stay green and untouched.

## 2. Approach (locked)

**A — Panel-vectorized signal → event-driven portfolio.** Hybrid:
- **Signal layer (vectorized, fast):** strategy consumes a panel `(T × N assets)` and
  emits a **target-weight matrix** `W[t, asset] ∈ [−1, +1]` (sign = direction, negative
  = short).
- **Execution layer (event-driven):** one loop over time `t` reads `W[t,:]`, rebalances a
  multi-asset portfolio, accrues funding, applies costs, enforces session + risk rules.

**Extend `crates/bt-engine` in place = ADDITIVE.** New portfolio path
(`portfolio_engine.rs`, `run_panel()`); existing `tick()`/`run()`/`StepEngine` (Phase 4
PPO) are **not mutated**. `cargo test -p bt-engine` (**28** `#[test]` cases) stays green
throughout.

**Perp accounting is a NEW path, not a spot special case** (Codex review). The current
engine is spot/cash long-only; it is *not* a reusable base for **signed positions +
collateral/margin** accounting. `portfolio_engine.rs` implements its own perp model
(signed qty, collateral, funding). The only shared-base claim we make: an *optional*
golden-parity test that `N=1, w∈{0,+1}` reproduces the spot `run()` equity curve — built
only if we decide that regression guard is worth it, not assumed.

## 3. Data model

### 3.1 Panel
Aligned multi-asset OHLCV. **Long format** parquet (one row per `(timestamp, asset)`):

| col | type | note |
|---|---|---|
| `timestamp` | i64 ms | shared clock across assets |
| `asset` | str | `xyz:SMSN`, … (universe member) |
| `open/high/low/close/volume` | f64 | per-asset bar |
| `funding_rate` | f64 | perp funding for this bar (0 for non-perp) |
| `session` | u8 | session tag (see 3.3) |
| `sector` | str | for cross-sectional neutralization (semis/auto/…) |

Loaded → pivoted to wide arrays `close[T,N]`, `funding[T,N]`, … at the NumPy boundary
(Rust gets contiguous `f64` matrices, same pattern as today).

**Universe membership over time** is explicit: a `(timestamp, asset, active)` mask so an
asset absent for part of the window is `NaN`/inactive, not silently zero. Guards
survivorship.

### 3.2 Funding
Per-asset `funding_rate[t]` from HL `fundingHistory`, **read from a parquet column**
(import-only — the fetch lives in the `korea-stock`/`upbit` sibling, see §10/§12). Accrued
each bar on open perp notional: `funding_pnl_t = −position_notional · funding_rate[t]`
(long pays positive funding, short receives). Carry leg — handoff established HL funding
≈ 23%/yr, NOT Binance listing-spike numbers.

### 3.3 Sessions (time-aware)
Each bar tagged `session` from a KR exchange calendar (KRX hours, holidays).
**Storage convention:** `timestamp` stored as **UTC ms**; session computed in
**Asia/Seoul (KST, UTC+9)**. `session: u8` enum — `0=closed, 1=premarket, 2=open,
3=mid, 4=close, 5=overnight`. Enables:
- session-conditioned weights (enter at open, flat overnight, ORB at session boundary),
- **time-neutral annualization** — Sharpe scaled by *actual exposure-hours*, not bar
  count. Handoff: overnight 27.7h vs intraday 6.0h asymmetry makes equal-notional ≠
  time-neutral. Engine reports both raw and time-neutral metrics.

## 4. Strategy API (signal layer)

```python
class PanelStrategy(Protocol):
    def compute(self, panel: PanelView) -> WeightMatrix:
        """Return W[t, asset] in [-1, +1]. sum_a |W[t,a]| <= leverage_cap."""
```

- `PanelView` exposes aligned arrays + `session`/`sector`/`funding` + helpers
  (cross-sectional rank, sector-demean, vol-scale).
- Output `W`: target **fraction of equity** per asset, signed. `0` = flat.
- Existing single-asset strategies wrap trivially (`N=1`, emit `{0,+1}`).

**Invalid-weight handling (explicit — never silent clamp).** Current `tick()` silently
clamps signals to `[0,1]`; that is *forbidden* on the portfolio path (it would mask
leverage/risk-cap violations). At `run_panel()` ingestion each row `W[t,:]` is validated:
- `|W[t,a]| > 1`, `gross = Σ|W| > leverage_cap`, `|net = ΣW| > max_net`, or per-asset cap
  breach → **reject by default** (raise `WeightViolation` with `t`, asset, value).
- Opt-in `on_violation: "renormalize"` config scales the row to the binding cap and logs;
  `"reject"` (default) hard-fails. No mode silently clamps.

### 4.1 Cross-sectional rotation primitive
`CrossSectionalStrategy(PanelStrategy)` base — at each rebalance `t`, **fixed order**:
1. score each active asset (momentum, flow, custom),
2. rank → select top-k long / bottom-k short,
3. raw weight (equal / score-prop / inverse-vol),
4. optional **sector-neutralize** — demean weights within `sector` (after selection),
5. **vol-scale** (optional),
6. **cap/renormalize** to `leverage_cap`/`max_net`; if still violated → reject per §4
   `on_violation`,
7. optional **time/regime gate** ("buy after N bars from open" / regime filter) — covers
   "buy certain sector or stocks after time goes". Gate applied last (zeroes weights when
   gate is closed).

### 4.2 Pairs / long-short primitive
`PairsStrategy(PanelStrategy)` — dollar-neutral leg pairs (e.g., long `xyz:SMSN` /
short `xyz:NVDA` on spread z-score), `W` sums to ~0 gross-balanced.

## 5. Portfolio event engine (Rust, additive)

`PortfolioEngine` in `crates/bt-engine/src/portfolio_engine.rs`. Per-bar `step`:

State: `cash`, `positions: {asset → qty}` (qty signed), `entry_price`, `equity`,
`peak_equity`, per-asset `unrealized_pnl`, `funding_paid_cum`, `turnover_cum`.

**Strict per-bar order** (fixes ambiguity — funding before fills, fills next-bar):
1. **Fill enqueued orders** from `t−1` at `open[t] + slippage` (next_bar model), pay
   taker fee. Update `positions`, `cash`/`collateral`.
2. **Funding accrual** on positions held over `[t−1,t]` (`funding_rate[t] · notional`,
   sign per long/short). Applied on the *pre-rebalance* book.
3. **Mark-to-market** at `close[t]` → `equity`, per-asset `unrealized`, `peak_equity`.
4. **Target notional** per asset `= equity · W[t,a]` (W already validated per §4).
5. **Rebalance diff** vs current book → new orders **enqueued for `t+1`** (no same-bar
   lookahead). `immediate` model fills at `close[t]` instead, for ablation only.
6. **Collateral/margin:** signed `qty` allowed; check maintenance margin against
   `equity`; short carry = funding sign flip (no separate borrow on HL perp).
7. **Risk gates:** max gross, max net, per-asset cap, drawdown halt (existing risk config,
   extended to portfolio) — breach halts new exposure.
8. **Session rules:** force-flat at configured session end (enqueue flatten for `t+1`, or
   `immediate` at session-close bar if configured).

Returns: equity curve, per-asset trade log, `funding_pnl`, turnover, gross/net exposure
series.

## 6. Costs & realism
- `slippage_bps`, taker `fee_rate`, **funding** (perp), optional `borrow_bps`.
- Fill `next_bar` default — order at `t` enqueued, filled at `open[t+1] + slippage`. No
  same-bar lookahead.
- Per-leg costs on both sides of a pair (round-trip both legs).

## 7. Walk-forward / OOS harness (anti-overfit guardrail)

The 12-angle null result was earned by rigorous OOS; this engine bakes it in.

- **Splits:** rolling or anchored train/test folds by date.
- **Purge + embargo** (de Prado): gap between train/test to kill leakage from
  overlapping-horizon labels.
- **Per fold:** tune params (Optuna TPE, existing) on train → evaluate frozen on test →
  aggregate **OOS** metrics only.
- **Multiple-testing correction:** Deflated Sharpe Ratio + BH-FDR across the trial set;
  optional PBO (probability of backtest overfit). A strategy that needs `k` trials to look
  good is discounted accordingly.
- Output: OOS Sharpe distribution, DSR, PBO, per-fold stability. A single in-sample Sharpe
  is never the headline.

## 8. Metrics
Portfolio + per-asset: Sharpe, Sortino, max_dd, turnover, `funding_pnl`, gross/net
exposure, hit rate, **long/short attribution**, **time-neutral Sharpe**. Raw and
time-neutral always reported side by side.

**Time-neutral Sharpe — precise definition.** Per-bar return `r_t = Δequity_t / equity_{t−1}`.
Per-bar exposure-hours `h_t = Δt_bar(hours) · 1[gross_t > 0]` (flat bars contribute 0
exposure but are NOT dropped from the return series). Define exposure-weighted moments:
- `μ_h = Σ(r_t · h_t) / Σ h_t`, `σ_h = sqrt( Σ h_t·(r_t − μ_h)² / Σ h_t )`.
- Annualization constant `A = HOURS_PER_YEAR / mean(h_t over exposed bars)`; per-bar
  Sharpe `μ_h/σ_h` scaled by `sqrt(A)`. `HOURS_PER_YEAR = 8760` (24h perp) — KR-cash
  variants use trading-hours/year instead, set per dataset.
- **Gross** exposure weights `h_t` (capital-at-risk view); a net-neutral variant is
  reported separately for pairs. Rationale: handoff's overnight 27.7h vs intraday 6.0h
  asymmetry means equal-notional ≠ time-neutral; this weights returns by hours actually
  at risk.

## 9. CLI
**Separate command** `bt backtest-panel --panel <panel.parquet> --config <portfolio.yaml>`
(not a flag on `bt backtest` — keeps existing single-asset configs untouched, Codex rec).
Config adds: `universe`, `leverage_cap`, `max_gross/net`, `on_violation`, `session_rules`,
`funding: true`, `walk_forward: {scheme, folds, embargo}`. `bt backtest` unchanged.

## 10. Build phases (additive, crypto path green each step)
- **P0** — Python, **import-only** (no network fetch in this repo): panel loader
  (long→wide pivot of an *already-imported* parquet), KRX session tagger (operates on
  stored UTC ms), HL funding **reader** (parquet column). The actual HL/KRX fetching lives
  in the `korea-stock`/`upbit` siblings; data arrives here via `bt data import`.
- **P1** — Rust `PortfolioEngine` + **2D PyO3 binding** (`run_panel()` accepting `(T×N)`
  matrices — current `python.rs` is 1D-only, so the binding ships in P1 or P2 Python
  strategies can't integration-test it) + unit tests (funding accrual, short PnL,
  rebalance, session-flat, weight-violation reject). Old engine untouched.
- **P2** — `PanelStrategy` API + 2 reference strategies (pairs, cross-sectional rotation),
  integration-tested end-to-end against the P1 binding.
- **P3** — walk-forward harness (purged CV + DSR/PBO).
- **P4** — CLI wiring + metrics report.
Each phase ends with `cargo test -p bt-engine` + `pytest` green.

## 11. Risks / open questions
- **Thin data:** HL `xyz` 6 assets × 105 days. OOS folds will be small → DSR/PBO matter
  more than point Sharpe. Likely underpowered for strong claims.
- **HL xyz longevity/liquidity** unknown — engine is correct even if this venue dies; data
  is the constraint.
- **Short carry** on HL = funding sign only (no separate borrow) — confirm per-asset.
- **KIS minute semantics** (single-call full session) for any KIS-sourced bars.
- **Survivorship / universe drift** — handled by active-mask, but membership history must
  be curated.
- **Overfit despite OOS** — small N; treat any surviving edge as provisional.

## 12. Out of scope
- Data fetching (moved to siblings; this repo is import-only).
- Crypto microstructure (`replay` engine stays separate).
- Live execution / order routing.

## 13. LLM agent flow (TradingAgents) — explicit boundary
Evaluated `TauricResearch/TradingAgents` (multi-agent LLM: analysts → bull/bear debate →
trader → risk/PM → simulated exchange). **Rejected as a backtest-engine flow**, for the
record so it isn't re-litigated:
- It is a *decision generator*, not a portfolio engine — our engine is the layer it lacks.
- **Backtesting an LLM agent on recent KR data is leakage-poisoned**: the model's training
  data already contains the outcomes (SMSN/SKHX paths, HBM-cycle resolution, news). Any
  historical "backtest" of its decisions manufactures fake alpha. Fatal for our OOS
  discipline (12-angle null was earned honestly).
- Horizon mismatch (daily fundamental/news vs our intraday session/perp edge); LLM-per-bar
  cost makes walk-forward CV infeasible; non-deterministic → breaks reproducibility;
  contradicts our own finding (edge = thesis/positioning beta, not a system signal).
- **Permitted use, if any:** a *forward-only, non-backtested* thesis/positioning layer
  (daily DART + KR-news → directional bias / sector overweight), feeding the engine a bias,
  never a backtested intraday trader. Validated live/paper, not on memorized history.
