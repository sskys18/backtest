# 2026-06-11 — On-chain fundamentals, cross-sectional (CoinMetrics)

## Hypothesis

Per-coin **on-chain fundamental** data (valuation + usage) — a family
structurally different from price/flow (6× null) and funding (compressed).
Three hypotheses, directions FIXED ex-ante (no sign mining):

- `mvrv` — valuation mean-reversion: long low MVRV / short high
- `adrg` — network-growth momentum: long high active-address growth / short low
  (7d/90d and 30d/180d log-ratio variants)
- `nvt` — value: long low mcap-per-transfer / short high

## Data

- `scripts/fetch_coinmetrics_onchain.py` — CoinMetrics **community** API
  (free, keyless): `AdrActCnt, TxCnt, TxTfrCnt, CapMVRVCur, CapMrktCurUSD,
  FeeTotNtv`, daily, 2019-01 → 2026-06.
- Coverage caveat: community tier ∩ Binance perps = **34 assets, majors only**
  (BTC/ETH/ADA/LINK/UNI/DOGE… — the most-arbitraged coins). Small-cap on-chain
  needs paid Glassnode/CM-pro. 80,369 asset-days.
- Signals lagged **2 days** for publication lag (conservative).

## Method

`scripts/backtest_onchain.py` — same engine as the funding test: daily
dollar-neutral quantile portfolio on USDT perps, PnL = price − funding paid −
7 bp/side turnover. IS 70 % grid (4 signals × q∈{.2,.3} = 8 configs) →
single-shot OOS, shuffle placebo ×200, DSR, CSCV PBO. Extra check: daily
cross-sectional Spearman IC of each signal vs trailing 30d return — is the
"fundamental" just momentum in disguise?

## Results

Momentum-disguise IC: mvrv **+0.22**, adrg-7/90 **+0.26**, adrg-30/180 +0.18,
nvt −0.02. MVRV and address-growth are partially repackaged momentum; NVT is
orthogonal.

IS grid: best adrg-30/180 q=.2 at SR **+0.52** (vs funding test's +2.46 —
nothing here even looks strong in-sample). mvrv negative IS. **PBO = 0.58**
(coin-flip overfitting).

Single-shot OOS (IS-best, adrg-30/180 q=.2):

| metric | value |
|---|---|
| OOS Sharpe | **−0.17** |
| OOS ann. ret | −3.8 % |
| OOS maxDD | −44 % |
| DSR (8 trials) | 0.21 |
| placebo | beats 198/200, but placebo mean SR −1.96 → real ≈ noise floor above random-cost-drag |

## Verdict

**Null.** On-chain fundamentals on liquid majors carry no tradeable
cross-sectional edge:

1. The two signals with any in-sample life (MVRV, address growth) are
   ~25 % momentum in disguise — and momentum itself is dead here.
2. NVT, the one orthogonal fundamental, is flat everywhere.
3. PBO 0.58 + OOS DSR 0.21 + negative OOS = textbook no-edge.

Consistent with the meta-conclusion mechanism: anything computable from free
public data on liquid venues is arbitraged. Untested residual: small-cap
on-chain (no free coverage), and *event-type* on-chain data (unlocks, exchange
inflows) which is a different data class than daily aggregates.

## Files

- `scripts/fetch_coinmetrics_onchain.py`, `scripts/backtest_onchain.py`
- `data/raw/coinmetrics_onchain_1d.parquet` (gitignored)
