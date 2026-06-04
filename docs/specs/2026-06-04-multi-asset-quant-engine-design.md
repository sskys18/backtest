# Multi-Asset Quant Backtest Engine — Design

> Date: 2026-06-04 (re-scoped 2026-06-04) · Status: **Re-scoped, awaiting user sign-off** · Approach: **A** (approved)
> Supersedes scope of: `docs/specs/2026-04-13-backtest-system-design.md` (single-asset long-only MVP)
> Reference repos: `docs/references/2026-06-04-engine-reference-repos.md` (borrow map per layer)

## 1. Goal

A **general multi-asset quant backtest engine** — backtest any cross-sectional /
long-short / portfolio strategy across **both intraday and mid-term (daily) horizons**.
Core capability: consume a multi-asset panel, emit signed target weights, execute against a
realistic event-driven portfolio with costs/funding/risk, and judge it under a rigorous
anti-overfit (walk-forward + DSR/PBO) harness. The current `bt-engine` cannot express any
of it — long-only, single-instrument, no funding, bar-agnostic time.

**First reference strategy** (drives the initial build, not the definition): KOSPI-major
long-short on perp (Hyperliquid `xyz` DEX — `xyz:SMSN`/`xyz:SKHX`/`xyz:HYUNDAI` legs vs
`xyz:NVDA`/`xyz:MU`/`xyz:TSM` hedge legs) + cross-sectional sector/stock rotation ("after
time/regime, rotate into a sector or names"). These are *configured universes/strategies*,
not baked into the engine.

**Horizon-agnostic.** Intraday (session-aware, perp funding, exposure-hour metrics) and
mid-term (daily bars, multi-day holds, standard annualization) are both first-class, gated
by `bar_freq` config (see §3.3, §8). The engine is correct at either frequency.

**Sector-analysis agents are out of the backtest boundary** — a *forward-only* thesis layer
(§13), never a backtested signal. Backtesting LLM agents on recent KR data is
training-leakage (fake alpha); mid-term horizons make it worse.

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
| `funding_rate` | f64 | perp funding for this bar (0 for non-perp / spot) |
| `session` | u8 | session tag (see 3.3). Daily *trading* bar = `2=open`; `0=closed` only for non-trading days; 24/7 (crypto) = always `2=open` |
| `sector` | str | for cross-sectional neutralization (semis/auto/…) |

Loaded → pivoted to wide arrays `close[T,N]`, `funding[T,N]`, … at the NumPy boundary
(Rust gets contiguous `f64` matrices, same pattern as today).

**Universe membership over time** is explicit: a `(timestamp, asset, active)` mask so an
asset absent for part of the window is `NaN`/inactive, not silently zero. Guards
survivorship.

### 3.2 Funding
Per-asset `funding_rate[t]` **read from a parquet column** (import-only — fetch lives in
the `korea-stock`/`upbit` sibling, see §10/§12); `0` for spot / non-perp datasets. Accrued
each bar on open perp notional: `funding_pnl_t = −position_notional · funding_rate[t]`
(long pays positive funding, short receives). **Bar-freq-aware:** the column already holds
the per-bar rate, so daily bars carry the daily-aggregated rate, 1-min bars the per-min
rate — the engine multiplies notional × the column value regardless of frequency. Carry
leg reference — handoff established HL funding ≈ 23%/yr, NOT Binance listing-spike numbers.

### 3.3 Sessions (time-aware — intraday only, config-gated)
**Gated by `bar_freq` / `session_aware` config.** Two modes:

- **Intraday, `session_aware: true`** — each bar tagged `session` from a KR exchange
  calendar (KRX hours, holidays). **Storage convention:** `timestamp` stored as **UTC ms**;
  session computed in **Asia/Seoul (KST, UTC+9)**. `session: u8` enum — `0=closed,
  1=premarket, 2=open, 3=mid, 4=close, 5=overnight`. Enables session-conditioned weights
  (enter at open, flat overnight, ORB at session boundary) and exposure-hour metrics (§8).
- **Mid-term / daily, `session_aware: false`** (default for daily bars) — each bar is
  `2=open` on a trading day, `0=closed` on a non-trading day (24/7 crypto = always `2=open`);
  no intraday session rules, no force-flat-at-close. Annualization uses the standard calendar
  path (§8). KR-cash daily, crypto daily, and any session-agnostic panel run here with zero
  intraday session machinery.

The engine picks the path from config; neither is hardcoded. A daily KR-cash sector-rotation
strategy and an intraday KR-perp pairs strategy run on the same engine, different config.

### 3.4 Currency / numeraire
A general panel may mix instruments quoted in different currencies (KR-cash = KRW,
crypto/HL perp = USD). The equity curve is computed in a single **`numeraire`** (config,
default `USD`). Each non-numeraire asset carries an `fx_rate[t]` column (point-in-time, same
clock); notional/PnL convert at `fx_rate[t]` before aggregation. Single-currency panels (the
first KR-perp-on-HL strategy = all-USD) set `fx_rate ≡ 1` and skip conversion. FX series are
import-only (sibling-sourced), never fetched here. Mixed-currency aggregation without a
declared numeraire is rejected at load.

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
- **Horizon-agnostic:** identical API for intraday and daily — the strategy reads whatever
  bars the panel holds. A daily sector-rotation and an intraday pairs strategy are both just
  `PanelStrategy` implementations.
- **Rebalance cadence is independent of `bar_freq`:** `rebalance_freq` config (e.g. weekly
  rebalance on daily bars, or every-bar). On non-rebalance bars the engine does **not** re-trade
  to targets — existing positions drift with price (§5 steps 4–5 skipped); only risk gates and
  margin force-reduce can still fire. Mid-term strategies routinely rebalance far less often
  than the bar.

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

**Strict per-bar order** (funding accrues on the position actually held over the interval,
*before* any new fills; fills next-bar):
1. **Funding accrual** on the **carried `t−1` book** (positions held over `[t−1,t]`):
   `funding_pnl = −position_notional · funding_rate[t]`, sign per long/short. Computed on the
   pre-fill book so newly-opened positions are not charged for an interval they did not span.
   No-op when `funding_rate ≡ 0` (spot/cash).
2. **Fill enqueued orders** from `t−1` at `open[t] + slippage` (next_bar model), pay taker
   fee. Update `positions`, `cash`/`collateral`. (New positions begin accruing funding from
   `t` onward, i.e. at the *next* bar's step 1.)
3. **Mark-to-market** at `close[t]` → `equity`, per-asset `unrealized`, `peak_equity`.
   (All cash flows — funding step 1, MTM here, fees — booked in `numeraire` via `fx_rate[t]`,
   §3.4.)
4. **Target notional** per asset `= equity · W[t,a]` (W already validated per §4). **Only on
   a rebalance bar** (per `rebalance_freq`, §4); on non-rebalance bars this step is skipped and
   existing positions **drift** with price (no re-trade) — that is the point of `rebalance_freq`.
5. **Rebalance diff** vs current book → new orders **enqueued for `t+1`** (no same-bar
   lookahead). Skipped on non-rebalance bars (per step 4). `immediate` model fills at
   `close[t]` instead, for ablation only. Risk gates (step 7) and margin force-reduce (step 6)
   still fire every bar regardless of rebalance schedule.
6. **Collateral/margin:** signed `qty` allowed; check maintenance margin against `equity`;
   short carry = funding sign flip (no separate borrow on HL perp). **On breach** (equity <
   maintenance requirement): force-reduce existing positions toward the maintenance bound
   (enqueue de-risk orders for `t+1`, or `immediate` at `close[t]` if configured) — distinct
   from step 7, which only halts *new* exposure. Total wipeout → flat + halt.
7. **Risk gates:** max gross, max net, per-asset cap, drawdown halt (existing risk config,
   extended to portfolio) — breach halts new exposure.
8. **Session rules** (intraday, `session_aware: true` only — skipped in daily mode): force-flat
   at configured session end (enqueue flatten for `t+1`, or `immediate` at session-close bar
   if configured).

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
  overlapping-horizon labels. **Embargo scales with holding period** — an intraday strategy
  needs hours; a mid-term strategy holding N days needs ≥N-day embargo. Configured per run,
  not a fixed constant.
- **Per fold:** tune params (Optuna TPE, existing) on train → evaluate frozen on test →
  aggregate **OOS** metrics only.
- **Multiple-testing correction:** Deflated Sharpe Ratio + BH-FDR across the trial set;
  optional PBO (probability of backtest overfit). A strategy that needs `k` trials to look
  good is discounted accordingly.
- Output: OOS Sharpe distribution, DSR, PBO, per-fold stability. A single in-sample Sharpe
  is never the headline.

## 8. Metrics
Portfolio + per-asset: Sharpe, Sortino, max_dd, turnover, `funding_pnl`, gross/net
exposure, hit rate, **long/short attribution**. Always reported.

**Annualization is mode-gated** (`session_aware`, §3.3):
- **Daily / mid-term (`session_aware: false`, default for daily):** standard calendar
  annualization — per-bar Sharpe `μ/σ` scaled by `sqrt(periods_per_year)` (`252` trading
  days for KR-cash, `365` for 24/7 crypto daily, set per dataset). No exposure-hour
  weighting; flat days count as zero-return days normally. This is the common case.
- **Intraday (`session_aware: true`):** additionally reports **time-neutral Sharpe** (below)
  because intraday exposure-hours are asymmetric. Raw and time-neutral side by side.

**Time-neutral Sharpe — intraday only, precise definition.** Per-bar return `r_t = Δequity_t / equity_{t−1}`.
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
Config adds: `universe`, `bar_freq` (`1m`/`1h`/`1d`/…), `rebalance_freq`, `session_aware`
(bool), `numeraire` (default `USD`), `periods_per_year` (annualization, daily mode),
`leverage_cap`, `max_gross/net`, `on_violation`, `session_rules` (intraday only),
`funding: true`, `walk_forward: {scheme, folds, embargo}`. `bt backtest` unchanged.

## 10. Build phases (additive, crypto path green each step)
- **P0** — Python, **import-only** (no network fetch in this repo): **bar-freq-aware** panel
  loader (long→wide pivot of an *already-imported* parquet; reads `bar_freq` from data/config),
  KRX session tagger (intraday only, operates on stored UTC ms; no-op for daily),
  funding **reader** (parquet column, `0` for spot). Data arrives via `bt data import`.
- **P1** — Rust `PortfolioEngine` + **2D PyO3 binding** (`run_panel()` accepting `(T×N)`
  matrices — current `python.rs` is 1D-only, so the binding ships in P1 or P2 Python
  strategies can't integration-test it) + unit tests (funding accrual on carried book, short
  PnL, rebalance, margin-breach force-reduce, session-flat, weight-violation reject,
  `bias` point-in-time gate). **Optional golden-parity test** (§2: `N=1, w∈{0,+1}` reproduces
  spot `run()`) is a P1 test if built. Old engine untouched.
- **P2** — `PanelStrategy` API + **2 primitives** (`PairsStrategy` §4.2, `CrossSectionalStrategy`
  §4.1) exercised by **3 reference configs**: intraday pairs (KR-perp), intraday CS rotation,
  **and a daily/mid-term sector-rotation** (a `CrossSectionalStrategy` instance, not a new
  primitive — proves the daily path end-to-end). Integration-tested against the P1 binding.
- **P3** — walk-forward harness (purged CV + DSR/PBO + horizon-scaled embargo).
- **P4** — CLI wiring (`bt backtest-panel`) + mode-gated metrics report (daily + intraday).
Each phase ends with `cargo test -p bt-engine` + `pytest` green.

**Out of P0–P4 entirely:** the forward-only thesis/agent layer (§13). It is a separate,
later track — never gated into the backtest engine build.

## 11. Risks / open questions
- **Thin data (first reference strategy, not the engine):** HL `xyz` 6 assets × 105 days.
  OOS folds will be small → DSR/PBO matter more than point Sharpe. Likely underpowered for
  strong claims on *that* universe. The engine itself is universe-agnostic; richer panels
  (KR-cash daily history, crypto) give it more room.
- **HL xyz longevity/liquidity** unknown — engine is correct even if this venue dies; data
  is the constraint.
- **Short carry** on HL = funding sign only (no separate borrow) — confirm per-asset.
- **KIS minute semantics** (single-call full session) for any KIS-sourced bars.
- **Survivorship / universe drift** — handled by active-mask, but membership history must
  be curated.
- **Adjusted prices (KR-cash daily)** — import-only assumes the sibling delivers
  split/dividend-adjusted (total-return) OHLCV. Raw unadjusted prices would inject phantom
  gaps. The loader does not adjust; the assumption must hold upstream (validate at import).
- **Overfit despite OOS** — small N; treat any surviving edge as provisional.

## 12. Out of scope
- Data fetching (moved to siblings; this repo is import-only).
- Crypto microstructure (`replay` engine stays separate).
- Live execution / order routing.

## 13. Sector-analysis agents — forward-only thesis lane (named architecture)

The system has **two lanes** with a hard wall between them:

```
  ┌─ BACKTEST LANE (§1–12) ──────────────┐   ┌─ THESIS LANE (this §, forward-only) ─┐
  │ panel → weights → portfolio engine   │   │ sector-analysis agents (LLM):        │
  │ → walk-forward OOS → DSR/PBO         │   │   DART + KR-news → directional bias  │
  │ historical, reproducible, leakage-   │   │   / sector overweight                 │
  │ guarded                              │ ← │ NEVER backtested; validated live/    │
  └──────────────────────────────────────┘   │ paper forward only                   │
            ↑ consumes a forward bias as an   └──────────────────────────────────────┘
              input, never as a backtested signal
```

**The agents are real and wanted** — they analyze each sector (semis/auto/…) to produce a
forward directional/overweight bias. But they live **outside the backtest boundary**, by
design, for one non-negotiable reason:

- **Backtesting an LLM agent on recent KR data is leakage-poisoned**: the model's training
  data already contains the outcomes (SMSN/SKHX paths, HBM-cycle resolution, news). Any
  historical "backtest" of its decisions manufactures fake alpha. Fatal for our OOS
  discipline (12-angle null was earned honestly). **Mid-term horizons make this worse** —
  longer holds map onto exactly the memorized fundamental narratives.
- LLM-per-bar cost makes walk-forward CV infeasible; non-deterministic → breaks
  reproducibility; contradicts our own finding (edge = thesis/positioning beta, not a
  backtestable system signal).

**Permitted, by design:** a *forward-only, non-backtested* thesis layer (daily DART +
KR-news → directional bias / sector overweight) that **feeds the engine a forward bias** —
e.g. tilts a universe or sets a sector-overweight prior on a *go-forward* run. Validated
live/paper, never on memorized history. Reference frameworks (forward-use only, do NOT
backtest): `TauricResearch/TradingAgents`, `AI4Finance/FinRobot`.

**Engine seam (leak-proofed — this is the critical constraint).** A naive "static `bias`
vector applied across the backtest window" **would smuggle lookahead**: a thesis formed today,
applied to historical bars, lets the backtest profit from knowing what happened (e.g.
"overweight semis" derived in Jun-2026, applied to a Jan–Jun-2026 fold). That is exactly the
leakage this section bans. Two hard rules close the hole:

1. **Inside the backtest / walk-forward harness (§7): `bias` is FORBIDDEN by default.** A
   backtest run rejects any non-null `bias` unless it is supplied as a **point-in-time series
   `bias[t]`** where each `bias[t]` is provably derivable only from data available at `t`
   (timestamped at production, subject to the same purge + embargo as any feature). A scalar
   or window-constant bias is rejected — there is no "current view" in a backtest.
2. **Outside the backtest — live/paper forward-run mode only:** `bias` is injected as the
   thesis lane produces it, going forward from today. This is the *intended* use; it never
   touches a historical fold.

The engine treats `bias[t]` as an exogenous point-in-time tilt, has no knowledge of how it was
produced, and never trains on it. Enforcement lives at `run_panel()` ingestion (same gate as
the weight-violation check, §4), not in prose.
