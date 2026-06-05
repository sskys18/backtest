# Cross-sectional reversal (perp) + session alpha-search summary

> 2026-06-05. Reversal = the shortable mirror of the momentum/surge nulls.

## Reversal verdict: null.
Long losers / short winners, 5m, 155 liquid Binance alts, market-neutral perp.
- IS sharpe 1.24 (barely fits even in-sample), OOS NET −18.5% (sharpe −8.7), DSR 0.0004.
- Placebo (random selection, same mechanic): −19.8% ≈ real → NO signal content.
- 5-fold: 2/5 positive (both ~0%), median −17.2%.

**Why:** mean-reversion was real in KR small-caps but untradeable (short constraints
on illiquid names; see intraday_surge_findings). On Binance you CAN short — but the
names liquid enough to short are liquid enough that the edge is already arbitraged.
Liquidity ⊥ edge: the reversal premium lives where you can't capture it.

## Session alpha-search summary (all rigorous: single-shot OOS + placebo + DSR/PBO)
| signal | data | verdict |
|--------|------|---------|
| long-only alt basket vs BTC | daily 18mo | null (−91% vs −37%) |
| price surge (mom/vol) | 5m 60d | null (overfit, 1-trade OOS) |
| order-flow (taker/OI) | 1h 21d | null (placebo survived +213%) |
| breakout + volume | 5m 60d | signal REAL (passes placebo) but not tradeable (1/5 folds) |
| regime-gated breakout | 5m 60d | no salvage |
| cross-sectional reversal | 5m 60d | null (real≈placebo, DSR 0) |

## Meta-conclusion
Every PRICE/FLOW signal on LIQUID Binance alts is arbitraged. Only breakout+volume
had real signal content, and even that nets negative OOS (loses less than random).
The remaining categories that could persist are STRUCTURALLY DIFFERENT from price
prediction:
1. **Funding carry / basis** — a RISK PREMIUM (paid to hold the unwanted side of
   leverage), not a predictive pattern → can survive in liquid markets. UNTESTED.
   Free deep funding history on Binance.
2. **Illiquid-market edges** — the KR small-cap reversal is real but blocked by
   short constraints; a long-only fade variant or a different venue might capture it.
3. **Event/catalyst** (listing, unlock) — user deprioritized.

Discipline note: do NOT keep filter-shopping price signals (that IS overfitting).
Next test should be funding carry (different risk-premium category), then stop and
conclude if null.
