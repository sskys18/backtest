# Intraday surge-signal search — does a few-hour alt trade beat buy-hold?

> 2026-06-05. Question: discover an intraday signal that enters the strongest
> firing alt, rides a trailing stop a few hours, and beats buy-hold — net of fees.

## Verdict: clean trustworthy negative — no edge, gross OR net.

No generalizable edge. Configs look spectacular in-sample (single-shot IS Sharpe
5.71) and collapse out-of-sample. The eye-catching "+133% gross" is a data-snooping
mirage: on bars the config never trained on it returns −25% GROSS / −45% net.
Fees are not the killer — generalization failure is.

## Setup
- **Data:** Binance spot 5m, 155 liquid alts ($20M rolling-24h floor), BTC held
  out. 2026-04-07 → 2026-06-05 (60d, 16,997 bars). Median 21 liquid alts/bar.
- **Strategy** (`SurgeStrategy`, in `scripts/search_surge_signal.py`): per bar,
  when a signal fires go long the single strongest firing alt (top_k=1, info≤t),
  ride a CLOSE-resolution trailing stop until stop trips or max_hold elapses.
  Long-only, next-bar-open fills, 10bps fee + 5bps slippage.
- **Signals searched:** momentum (k-bar return), volume-spike (vol/MA), combo.
  Params: lookback 3–24 (15m–2h), entry_thresh 0.5–5%, vol_mult 1.5–5×,
  trail_pct 1–5%, max_hold 6–72 bars (30m–6h).
- **Harness:** `walk_forward` (rolling 4-fold, purge=embargo=72), Optuna
  tune-on-train → freeze → eval-OOS. **Real DSR** (deflated by the 88 distinct
  configs tested) + **real CSCV PBO**. First use of the search-space path.

## Walk-forward (supporting evidence — rolling 4-fold)
Aggregates FOUR different per-fold-tuned configs, so the headline number mixes
configs; use it for PBO/DSR, not as the single verdict (single-shot below is the
clean one).
| | value |
|--|--|
| distinct configs tested (trial count) | 88 |
| per-fold OOS sharpe | [−0.13, −6.97, −13.71, −2.60] |
| concat OOS cum (4 mixed configs) | −89.7% |
| BTC OOS cum (same bars) | −17.9% |
| **DSR** (deflated by 88 trials) | **1.2e-10** (≈0) |
| **PBO** (CSCV) | **0.74** |

Pre-registered edge criterion (DSR>0.95 AND OOS>BTC AND PBO<0.5): **fails all three.**

## Single-shot OOS (gold standard — tune IS 70%, freeze ONE config, eval untouched 30%)
| | value |
|--|--|
| IS train sharpe (looks great) | **5.71** |
| OOS cum (untouched 30%) | NET **−4.4%**, GROSS −4.1% |
| OOS trades | **1** |
| OOS DSR (deflated by 73 trials) | **0.0** |
| BTC OOS cum | −18.5% |

The IS-tuned config is so fitted to IS spikes it **fires once** in two weeks of
fresh data. NET≈GROSS → fees are irrelevant here; the failure is generalization.

## The "+133% gross" mirage, dissected
Cherry-picked config (combo, lb=18, thresh=5%, vol=5×, trail=3%, hold=72):
| run | gross | net | trades |
|-----|-------|-----|--------|
| FULL sample (incl. selection region) | **+133%** | +24% | 97 |
| **FRESH bars [6798:end]** (never trained/selected) | **−25.3%** | **−45.0%** | 48 |

The +133%/+24% lived entirely in the in-sample region. On fresh bars the SAME
config loses 25% gross / 45% net. So there is **no robust gross signal** — the
apparent edge is pure data-snooping (PBO=0.74 confirms IS-winners land below OOS
median 74% of the time).

## Attribution
1. **No generalizable signal** — single-shot OOS DSR=0, 1 trade; fresh-bars gross
   −25%. The in-sample brilliance does not survive contact with untouched data.
2. **Fees are real but secondary** — per-trade (turnover 336M notional ≈ 194 fills
   on a growing NAV, not per-bar churn). They deepen the loss; they don't cause it.
3. **Selection is anti-predictive** — PBO=0.74; tuning maximizes Sharpe on ~11-day
   train windows with ~10–20 trades = fitting noise.

## Takeaway
Liquid Binance 5m alts: surge-continuation as searched has **no exploitable edge,
gross or net** — heavily arbitraged. Same conclusion class as the daily alt-vs-BTC
test, reached through the proper single-shot OOS + fresh-bars discriminator. Do
NOT cite the +133%/+24% full-sample numbers as signal — they are the textbook
overfit illusion this harness exists to expose. A credible retry would need a
genuinely different signal (microstructure: orderbook imbalance, trade-flow),
intra-bar fills (replay sim), and the same untouched-holdout discipline.

## Known limits (advisor)
Close-resolution trailing stop (not intra-bar); next-bar-open fills; flat
slippage fair only because liquid-only. Adverse-fill not modeled → real results
≤ these. Survivorship: live-symbol klines only.
