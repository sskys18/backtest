# 2026-06-11 — Funding carry (per-coin funding-rate data)

## Hypothesis

Funding rates are a **risk premium**, not a price-prediction signal — the one
per-coin data family structurally different from the six price/flow families
already proven null. Two tests:

- **A. Cross-sectional funding portfolio**: daily dollar-neutral, long
  bottom-quantile trailing funding / short top-quantile. Collects funding on
  both legs, pays price risk + fees.
- **B. Delta-neutral carry**: long spot / short perp, harvest the funding
  stream. No directional risk; PnL ≈ Σ funding − fees − basis drift.

## Data

- `scripts/experiments/fetch_binance_funding_panel.py` — **full-depth** funding history
  (unlike taker/OI's 500-point cap, `/fapi/v1/fundingRate` paginates to listing
  date; verified BTCUSDT from 2020-01-01). 530 USDT perpetuals, 1.90M funding
  events, 2019-09 → 2026-06. Plus daily perp klines (426k bars).
- Survivorship: fapi lists live contracts only. Delisted perps missing from
  BOTH legs → direction ambiguous for A; for B (long-side carry on majors) the
  bias is negligible.

## Method (rigor per protocol)

`scripts/experiments/backtest_funding.py`. IS = first 70 % (2019-09→2024-06), single-shot
OOS = last 30 % (2024-06→2026-06). Grid (IS only): W∈{3,7,14,30} × q∈{.1,.2} ×
liq-floor∈{5M,20M} = 16 configs. Costs 7 bp/side/turnover. Within-day shuffle
placebo ×200, DSR deflated by all 16 trials, CSCV PBO (16 splits).

## Results

### A. Cross-sectional funding — REAL SIGNAL, NOT TRADEABLE

| | IS | OOS (single-shot) |
|---|---|---|
| Sharpe | +2.46 (best: W=3 q=.2 liq=5M) | **−1.02** |
| Ann. ret | +51 % | **−21.5 %** |
| DSR | — | **0.004** |

- PBO (CSCV, 16 configs) = 0.30.
- OOS decomposition (ann.): **funding leg +51.7 %** / **price leg −62.7 %** /
  costs −10.5 %. The funding collection is real money, but shorting
  high-funding coins = shorting momentum — the price leg eats it whole.
- Placebo: real beats **200/200** shuffles (placebo mean SR −3.08). So the
  signal carries genuine information — same shape as the breakout screener:
  *real, but not tradeable net of the price leg*.

### B. Delta-neutral carry — REAL PREMIUM, CURRENTLY COMPRESSED

Funding-stream-only stats (before ~20 bp entry/exit + margin drag):

| asset | ann. carry (full) | SR | maxDD | pos. days |
|---|---|---|---|---|
| BTC | +11.8 % | 10.6 | −1.5 % | 87 % |
| ETH | +14.2 % | 9.8 | −1.8 % | 88 % |
| EW top-20-liq basket | +10.6 % | 6.1 | −5.1 % | 76 % |

Per-year EW-20 basket: 2020 +18 % · 2021 +36 % · 2022 −4 % · 2023 +6 % ·
2024 +13 % · **2025 +1.7 % · 2026 YTD −6.4 %**.

Trailing 365 d: BTC +3.6 %, ETH +3.0 %, basket **−0.6 %**. The premium has been
arbitraged down (institutional basis trade era) — currently **below risk-free**.

### B′. Conditional carry (regime-gated harvest — allowed category)

Enter only when trailing W-day basket funding > threshold (annualized); 20 bp
per regime flip. Small grid {7,30}×{5,10,20 %}, same IS/OOS split:

- IS-best (W=30, thr=5 %): **OOS +2.2 %/yr, SR 4.78, in-market 29 %** of days
  (≈ +7.6 %/yr while deployed). Unconditional OOS: +1.5 %/yr, SR 1.31.
- Gating works (avoids the negative stretches), but the absolute level is thin.

## Verdict

1. **Cross-sectional funding strategy: dead.** Real information, but it is the
   mirror of momentum — price leg −63 %/yr OOS swamps funding +52 %/yr.
2. **Funding carry: real risk premium, wrong decade.** 2020-21 paid 18-36 %/yr;
   post-2024 institutionalization compressed it to ≤ risk-free. Confirms the
   meta-conclusion mechanism: accessible premia on liquid venues get crowded.
3. **Salvage = conditional overlay, not a strategy.** Harvest only when
   trailing funding > ~5 % ann (W=30): OOS-positive (+7.6 %/yr deployed,
   SR 4.8) but only ~30 % deployment. Worth having as an opportunistic sleeve
   when funding spikes (bull manias), pointless as a standing allocation today.

## Files

- `scripts/experiments/fetch_binance_funding_panel.py` — funding + perp-klines fetcher
- `scripts/experiments/backtest_funding.py` — tests A and B
- `data/raw/binance_funding_panel.parquet`, `binance_perp_panel_1d.parquet`
  (gitignored)
