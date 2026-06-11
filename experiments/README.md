# Experiments Ledger

Single index of every alpha investigation in this repo. Date-stamped reports
are immutable records; this README is the only file that gets updated.

**Rigor protocol** (mandatory for any alpha claim): single-shot untouched OOS
+ shuffle placebo + DSR/PBO. Naive backtests lie — IS mirages of +133 %/+53 %
have collapsed under this protocol.

## Crypto — signal families (all tested under full rigor)

| date | experiment | verdict | report |
|---|---|---|---|
| 2026-06-05 | Long-only alt momentum basket vs BTC | ❌ null — loses to BTC buy-hold (−91 % vs −37 %); alt-beta is the killer | [report](2026-06-05-alt-vs-btc-longonly.md) |
| 2026-06-05 | 5m intraday surge | ❌ null — IS +133 % mirage; fresh-bars gross −25 %, PBO 0.74 | [report](2026-06-05-intraday-surge-signal.md) |
| 2026-06-05 | Order-flow microstructure (taker/OI 1h) | ❌ null — shuffle placebo also +213 % (pure noise) | [report](2026-06-05-microstructure-flow-signal.md) |
| 2026-06-05 | New-high breakout + volume surge | ⚠️ signal real (beats placebo 4/5) but OOS net-negative — watch tool, not auto-trade | [report](2026-06-05-screener-breakout-backtest.md) |
| 2026-06-05 | Regime-gated breakout (salvage) | ❌ failed — 1/5 folds, median −23 %; n≈1 regime in 60 d | in breakout report |
| 2026-06-05 | Cross-sectional reversal | ❌ null — real ≈ placebo, DSR 0.0004 | [report](2026-06-05-reversal-and-summary.md) |
| 2026-06-11 | Funding carry (cross-sectional + delta-neutral) | ⚠️ premium real but compressed post-2024 (≤ risk-free); only conditional harvest OOS+ (+7.6 %/yr deployed, 29 % exposure) | [report](2026-06-11-funding-carry.md) |
| 2026-06-11 | On-chain fundamentals (MVRV / addr-growth / NVT) | ❌ null — PBO 0.58, OOS −0.17 SR; MVRV & addr-growth ≈ 25 % momentum in disguise | [report](2026-06-11-onchain-fundamentals.md) |

**Meta-conclusion** (memory: `alt-alpha-meta-conclusion`): every signal family
computable from free public data on liquid venues is arbitraged — price/flow
×6, funding, on-chain fundamentals. Liquidity ⊥ edge. Do NOT re-mine variants
of dead families; that is filter-shopping overfit. Remaining unexplored:
kimchi premium / KR fragmentation (structural access moat), event-class
on-chain (unlocks, exchange flows — paid/live data), small-cap on-chain
(paid), live-collected microstructure.

## Crypto — microstructure scan-replay lineage (pre-rigor-protocol)

- `archive/replay_*.json`, `archive/intraday_surge_findings.md` — 18k+ scan
  events → entry-filter × TP/SL sweeps (April 2026).
- `scanner_tuning/*_best.yaml` — Optuna tuning of the 7 ported web scanners
  (`src/bt/scanners/`) on BTC-USDT 30 d 5m forward returns. **Pure IS
  optimization, no OOS/placebo/DSR** — treat best_params as unvalidated.
- 4 of 7 scanners depend on data capped at ~500 points by Binance free API
  (taker/OI/liq/LSR) → deep backtest impossible; live collection only.
  `kimp_wave` / `kimchi_audit` await a kimchi-premium dataset.

## Korean equities

- KR tech alpha, 4 angles — all null (supercycle regime; returns are
  positional beta). Memory: `kr-tech-alpha-findings`. Do not re-search.
- KRX sector rotation — panel-engine reference strategy (validation fixture,
  not an alpha claim). `configs/panel/kr_sector_rotation_daily.yaml`.

## Engine-tuning lineage (not alpha research)

- `runs/` (gitignored) — BTC-USDT 1h momentum parameter sweeps under
  `goals.md` (Sharpe-max framework, April 2026).
- `perp/` — perp-engine validation sweeps.

## Layout

```
experiments/
├── README.md            ← this ledger (the only mutable file)
├── goals.md             ← bt optimize objective spec
├── 2026-*.md            ← immutable date-stamped reports
├── archive/             ← legacy artifacts (scan-replay era)
├── scanner_tuning/      ← Optuna best-params YAMLs (unvalidated)
├── perp/                ← engine validation sweep scripts
└── runs/                ← run artifacts (gitignored)
```
